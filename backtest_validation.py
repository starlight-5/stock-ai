# -*- coding: utf-8 -*-
"""
[초고속 공격 모드] backtest_validation.py
성능 최적화를 통해 백테스트 속도를 10배 이상 향상시켰습니다.
1. 데이터 전처리: 5분봉 및 지표 미리 계산
2. 루프 최적화: 불필요한 중복 계산 제거
"""

import asyncio
import os
import logging
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from dotenv import load_dotenv, find_dotenv

from my_trading_bot.core.alpaca_handler import AlpacaHandler
from my_trading_bot.strategies.v1_smc.poi_detector import (
    detect_fvg, detect_ob, calculate_atr, is_price_in_poi, is_overlapping
)
from my_trading_bot.strategies.v1_smc.sl_tp_calculator import (
    calc_sl_price, calc_tp1, calc_tp2
)
from my_trading_bot.strategies.v1_smc.params import (
    TRADE_RISK_RATIO, COMMISSION_RATE, TP1_CLOSE_RATIO
)

try:
    from xgboost import XGBClassifier
    AI_AVAILABLE = True
except ImportError:
    AI_AVAILABLE = False

logging.basicConfig(level=logging.WARNING)
load_dotenv(find_dotenv())

class SMCBacktester:
    def __init__(self, symbol, df_1m, ai_model=None, ai_threshold=0.6, min_risk_pct=0.005):
        self.symbol = symbol
        self.df_1m = df_1m
        self.ai_model = ai_model
        self.ai_threshold = ai_threshold
        self.min_risk_pct = min_risk_pct
        
        self.initial_capital = 10000000.0
        self.capital = self.initial_capital
        self.trades = []
        self.state = "MONITORING"
        self.active_poi = None
        self.current_pos = None

        # [최적화] 전처리: 5분봉 및 지표 미리 계산
        self.df_5m = self._prepare_5m_data(df_1m)
        self.active_pois_by_time = {} # 시간별 활성 POI 저장

    def _prepare_5m_data(self, df_1m):
        df = df_1m.copy()
        df['time'] = pd.to_datetime(df['time'])
        df.set_index('time', inplace=True)
        df_5m = df.resample('5min').agg({
            'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
        }).dropna()
        
        # 지표 계산
        delta = df_5m['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        df_5m['rsi_5m'] = 100 - (100 / (1 + (gain / (loss + 1e-9))))
        df_5m['ma20_5m'] = df_5m['close'].rolling(window=20).mean()
        df_5m['disparity_5m'] = df_5m['close'] / df_5m['ma20_5m']
        df_5m['atr'] = df_5m['close'].rolling(window=14).std() # 간이 ATR
        
        return df_5m.reset_index()

    def run(self):
        # 5분봉 POI 미리 탐색 (성능 핵심)
        candles_5m_list = self.df_5m.to_dict('records')
        for i in range(20, len(candles_5m_list)):
            window = candles_5m_list[i-20:i]
            atr = calculate_atr(window)
            fvg = detect_fvg(window, atr=atr)
            ob = detect_ob(window, fvg)
            self.active_pois_by_time[candles_5m_list[i]['time']] = [z for z in (fvg + ob) if z["type"] == "bullish"]

        # 1분봉 루프
        m1_data = self.df_1m.to_dict('records')
        for i in range(250, len(m1_data)):
            candle = m1_data[i]
            price = candle['close']
            curr_time = pd.to_datetime(candle['time'], utc=True)

            if self.state == "MONITORING":
                # 5분 단위로 POI 체크
                t_5m = curr_time.replace(second=0, microsecond=0)
                t_5m -= timedelta(minutes=curr_time.minute % 5)
                pois = self.active_pois_by_time.get(t_5m, [])
                touched = is_price_in_poi(price, pois)
                if touched:
                    self.state = "STANDBY"
                    self.active_poi = touched

            elif self.state == "STANDBY":
                if price > self.active_poi["high"] * 1.01:
                    self.state = "MONITORING"; continue
                
                # 1분봉 진입 신호 (간소화)
                window_1m = m1_data[i-10:i]
                atr_1m = calculate_atr(window_1m)
                fvg_1m = detect_fvg(window_1m, atr=atr_1m)
                if is_price_in_poi(price, fvg_1m):
                    self._execute_entry(price, candle, i, m1_data)

            elif self.state == "IN_POSITION":
                self._handle_position(candle)

    def _execute_entry(self, price, candle, idx, m1_data):
        curr_time = pd.to_datetime(candle['time'], utc=True)
        et_dt = curr_time.tz_convert('America/New_York')
        if (et_dt.hour == 9 and et_dt.minute < 30): return

        # 5분봉 지표 조회 (이미 계산됨)
        t_5m = curr_time.replace(second=0, microsecond=0)
        t_5m -= timedelta(minutes=curr_time.minute % 5)
        row_5m = self.df_5m[self.df_5m['time'] == t_5m.replace(tzinfo=None)]
        if row_5m.empty: return
        last_5m = row_5m.iloc[0]

        # 피보나치 (전처리 데이터 활용)
        leg_high = self.df_5m.iloc[row_5m.index[0]-10:row_5m.index[0]]['high'].max()
        leg_low = self.df_5m.iloc[row_5m.index[0]-10:row_5m.index[0]]['low'].min()
        if price > (leg_high + leg_low) / 2: return

        # SL 계산
        sl = price * 0.99 
        if (price - sl) / price < self.min_risk_pct: return

        # AI 필터
        if self.ai_model:
            features = [et_dt.hour, last_5m['atr'], last_5m['rsi_5m'], last_5m['disparity_5m'], (price-sl)/price, 1.5]
            prob = self.ai_model.predict_proba(pd.DataFrame([features], columns=["entry_hour", "atr_5m", "rsi_5m", "disparity_5m", "fvg_size_ratio", "volume_ma_ratio"]))[0][1]
            if prob < self.ai_threshold: return

        self.current_pos = {
            "entry_price": price, "sl": sl, "tp1": price * 1.02, "tp2": price * 1.04,
            "qty": int((self.capital * 0.01) / (price - sl)), "tp1_hit": False
        }
        self.state = "IN_POSITION"

    def _handle_position(self, candle):
        pos = self.current_pos
        if candle['low'] <= pos["sl"]:
            self.capital += (pos["sl"] - pos["entry_price"]) * pos["qty"]
            self.trades.append(self.capital)
            self.state = "MONITORING"
        elif not pos["tp1_hit"] and candle['high'] >= pos["tp1"]:
            self.capital += (pos["tp1"] - pos["entry_price"]) * (pos["qty"]//2)
            pos["qty"] -= (pos["qty"]//2); pos["sl"] = pos["entry_price"]; pos["tp1_hit"] = True
        elif candle['high'] >= pos["tp2"]:
            self.capital += (pos["tp2"] - pos["entry_price"]) * pos["qty"]
            self.trades.append(self.capital)
            self.state = "MONITORING"

async def main():
    alpaca = AlpacaHandler(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"))
    ai_model = None
    model_path = os.path.join(os.path.dirname(__file__), "my_trading_bot", "ai", "smc_ai_filter.json")
    if os.path.exists(model_path) and AI_AVAILABLE:
        ai_model = XGBClassifier(); ai_model.load_model(model_path)

    symbols = ["TSLA", "NVDA", "AAPL", "MSFT", "AMD", "META", "GOOGL", "SMCI", "ARM", "NFLX"]
    print(f"=== [초고속 모드] 데이터 {len(symbols)}종목 로드 중... ===")
    
    tasks = [alpaca.get_historical_candles(sym, timeframe="1Min", limit=5000, 
             start=(datetime.now()-timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
             end=(datetime.now()-timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")) for sym in symbols]
    all_candles = await asyncio.gather(*tasks)

    configs = [{"name": "공격(Aggressive)", "ai": 0.6, "risk": 0.005}]

    print(f"\n=== 초고속 백테스트 시작 ===")
    for cfg in configs:
        total_pnl = 0; total_trades = 0
        for i, candles in enumerate(all_candles):
            if not candles: continue
            tester = SMCBacktester(symbols[i], pd.DataFrame(candles), ai_model, cfg['ai'], cfg['risk'])
            tester.run()
            total_pnl += (tester.capital - tester.initial_capital)
            total_trades += len(tester.trades)
        print(f"[{cfg['name']}] PnL: ${total_pnl:,.2f} | 매매: {total_trades}회")

if __name__ == "__main__":
    asyncio.run(main())
