# -*- coding: utf-8 -*-
"""
[초고속 공격 모드 V2] backtest_validation.py
시간대 불일치 문제를 해결하고 매매 활성화를 위해 필터를 재조정했습니다.
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
    detect_fvg, detect_ob, calculate_atr, is_price_in_poi
)
from my_trading_bot.strategies.v1_smc.params import TRADE_RISK_RATIO, COMMISSION_RATE

try:
    from xgboost import XGBClassifier
    AI_AVAILABLE = True
except ImportError:
    AI_AVAILABLE = False

logging.basicConfig(level=logging.WARNING)
load_dotenv(find_dotenv())

class SMCBacktester:
    def __init__(self, symbol, df_1m, ai_model=None, ai_threshold=0.6):
        self.symbol = symbol
        self.df_1m = df_1m
        self.ai_model = ai_model
        self.ai_threshold = ai_threshold
        
        self.initial_capital = 10000000.0
        self.capital = self.initial_capital
        self.trades = []
        self.state = "MONITORING"
        self.active_poi = None
        self.current_pos = None

        # 전처리: 5분봉 및 지표 미리 계산 (시간대 제거하여 통일)
        self.df_1m['time_naive'] = pd.to_datetime(self.df_1m['time']).dt.tz_localize(None)
        self.df_5m = self._prepare_5m_data(self.df_1m)
        self.active_pois_by_time = {} 

    def _prepare_5m_data(self, df_1m):
        df = df_1m.copy()
        df.set_index('time_naive', inplace=True)
        df_5m = df.resample('5min').agg({
            'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
        }).dropna()
        
        df_5m['atr'] = df_5m['close'].rolling(window=14).std().fillna(df_5m['close'] * 0.002)
        df_5m['rsi'] = 50.0 # 간이 RSI
        df_5m['ma20'] = df_5m['close'].rolling(window=20).mean()
        df_5m['disparity'] = df_5m['close'] / df_5m['ma20']
        
        return df_5m.reset_index()

    def run(self):
        # 5분봉 POI 미리 탐색
        m5_list = self.df_5m.to_dict('records')
        for i in range(20, len(m5_list)):
            window = m5_list[i-20:i]
            # calculate_atr 대신 간단한 변동성 사용 (속도 향상)
            atr = m5_list[i-1]['atr']
            fvg = detect_fvg(window, atr=atr)
            ob = detect_ob(window, fvg)
            self.active_pois_by_time[m5_list[i]['time_naive']] = [z for z in (fvg + ob) if z["type"] == "bullish"]

        # 1분봉 루프
        m1_data = self.df_1m.to_dict('records')
        for i in range(50, len(m1_data)):
            candle = m1_data[i]
            price = candle['close']
            t_naive = candle['time_naive']

            if self.state == "MONITORING":
                t_5m = t_naive.replace(second=0, microsecond=0)
                t_5m -= timedelta(minutes=t_naive.minute % 5)
                pois = self.active_pois_by_time.get(t_5m, [])
                touched = is_price_in_poi(price, pois)
                if touched:
                    self.state = "STANDBY"
                    self.active_poi = touched

            elif self.state == "STANDBY":
                if price > self.active_poi["high"] * 1.01:
                    self.state = "MONITORING"; continue
                
                # 1분봉 FVG 체크
                window_1m = m1_data[i-5:i]
                fvg_1m = detect_fvg(window_1m, atr=price*0.001)
                if is_price_in_poi(price, fvg_1m):
                    self._execute_entry(price, t_naive)

            elif self.state == "IN_POSITION":
                self._handle_position(candle)

    def _execute_entry(self, price, t_naive):
        # 5분봉 데이터 조회
        t_5m = t_naive.replace(second=0, microsecond=0)
        t_5m -= timedelta(minutes=t_naive.minute % 5)
        m5_row = self.df_5m[self.df_5m['time_naive'] == t_5m]
        if m5_row.empty: return
        
        # 피보나치 (0.5로 완화)
        idx = m5_row.index[0]
        leg_high = self.df_5m.iloc[max(0, idx-10):idx]['high'].max()
        leg_low = self.df_5m.iloc[max(0, idx-10):idx]['low'].min()
        if price > (leg_high + leg_low) / 2: return

        # AI 필터
        if self.ai_model:
            row = m5_row.iloc[0]
            features = [t_naive.hour, row['atr'], 50.0, row['disparity'], 0.01, 1.5]
            prob = self.ai_model.predict_proba(pd.DataFrame([features], columns=["entry_hour", "atr_5m", "rsi_5m", "disparity_5m", "fvg_size_ratio", "volume_ma_ratio"]))[0][1]
            if prob < self.ai_threshold: return

        sl = price * 0.99
        self.current_pos = {
            "entry_price": price, "sl": sl, "tp1": price * 1.015, "tp2": price * 1.03,
            "qty": int((self.capital * 0.01) / (price - sl))
        }
        self.state = "IN_POSITION"

    def _handle_position(self, candle):
        pos = self.current_pos
        if candle['low'] <= pos["sl"]:
            self.capital += (pos["sl"] - pos["entry_price"]) * pos["qty"]
            self.trades.append(self.capital); self.state = "MONITORING"
        elif candle['high'] >= pos["tp1"]:
            # 단순화를 위해 TP1에서 전량 익절로 테스트
            self.capital += (pos["tp1"] - pos["entry_price"]) * pos["qty"]
            self.trades.append(self.capital); self.state = "MONITORING"

async def main():
    alpaca = AlpacaHandler(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"))
    ai_model = None
    model_path = os.path.join(os.path.dirname(__file__), "my_trading_bot", "ai", "smc_ai_filter.json")
    if os.path.exists(model_path) and AI_AVAILABLE:
        ai_model = XGBClassifier(); ai_model.load_model(model_path)

    symbols = ["TSLA", "NVDA", "AAPL", "MSFT", "AMD", "META", "GOOGL", "SMCI", "ARM", "NFLX"]
    print(f"=== [초고속 모드 V2] {len(symbols)}종목 로드 중... ===")
    
    tasks = [alpaca.get_historical_candles(sym, timeframe="1Min", limit=10000) for sym in symbols]
    all_candles = await asyncio.gather(*tasks)

    print(f"\n=== 백테스트 시작 ===")
    total_pnl = 0; total_trades = 0
    for i, candles in enumerate(all_candles):
        if not candles: continue
        tester = SMCBacktester(symbols[i], pd.DataFrame(candles), ai_model, 0.6)
        tester.run()
        pnl = tester.capital - tester.initial_capital
        total_pnl += pnl
        total_trades += len(tester.trades)
        print(f"[{symbols[i]}] PnL: ${pnl:,.2f} | 매매: {len(tester.trades)}회")
    
    print(f"\n[최종 결과] 총 PnL: ${total_pnl:,.2f} | 총 매매: {total_trades}회")

if __name__ == "__main__":
    asyncio.run(main())
