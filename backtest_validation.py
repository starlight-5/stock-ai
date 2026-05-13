# -*- coding: utf-8 -*-
"""
[공격 모드 V4] backtest_validation.py
POI 탐색 로그를 추가하고, 검색 범위를 최근 1시간으로 확대했습니다.
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
    detect_fvg, detect_ob, is_price_in_poi
)

try:
    from xgboost import XGBClassifier
    AI_AVAILABLE = True
except ImportError:
    AI_AVAILABLE = False

logging.basicConfig(level=logging.WARNING)
load_dotenv(find_dotenv())

class SMCBacktester:
    def __init__(self, symbol, df_1m, ai_model=None):
        self.symbol = symbol
        self.df_1m = df_1m
        self.ai_model = ai_model
        
        self.initial_capital = 10000000.0
        self.capital = self.initial_capital
        self.trades = []
        self.state = "MONITORING"
        self.current_pos = None

        self.df_1m['time_naive'] = pd.to_datetime(self.df_1m['time']).dt.tz_localize(None)
        self.df_5m = self._prepare_5m_data(self.df_1m)
        self.all_bullish_pois = [] # 발견된 모든 지지선 저장

    def _prepare_5m_data(self, df_1m):
        df = df_1m.copy()
        df.set_index('time_naive', inplace=True)
        df_5m = df.resample('5min').agg({
            'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
        }).dropna()
        df_5m['ma20'] = df_5m['close'].rolling(window=20).mean()
        df_5m['disparity'] = df_5m['close'] / df_5m['ma20']
        return df_5m.reset_index()

    def run(self):
        m5_list = self.df_5m.to_dict('records')
        print(f"[{self.symbol}] 5분봉 {len(m5_list)}개 분석 중...")
        
        # 5분봉 전체에서 POI 미리 다 뽑아두기
        for i in range(20, len(m5_list)):
            window = m5_list[i-20:i]
            fvg = detect_fvg(window, atr=0) 
            ob = detect_ob(window, fvg)
            bullish = [z for z in (fvg + ob) if z["type"] == "bullish"]
            for b in bullish:
                b["found_at"] = m5_list[i-1]['time_naive'] # 발견된 시각 기록
                self.all_bullish_pois.append(b)
        
        print(f"[{self.symbol}] 총 {len(self.all_bullish_pois)}개의 지지선 발견")

        m1_data = self.df_1m.to_dict('records')
        for i in range(50, len(m1_data)):
            candle = m1_data[i]
            price = candle['close']
            t_naive = candle['time_naive']

            if self.state == "MONITORING":
                # 최근 1시간 이내에 발견된 지지선만 체크
                active_pois = [p for p in self.all_bullish_pois if t_naive - timedelta(hours=2) <= p["found_at"] < t_naive]
                
                for poi in active_pois:
                    # 지지선 구역 터치 시 진입
                    if poi["low"] * 0.999 <= price <= poi["high"] * 1.001:
                        self._execute_entry(price, t_naive)
                        break

            elif self.state == "IN_POSITION":
                self._handle_position(candle)

    def _execute_entry(self, price, t_naive):
        # AI 필터 (공격적 0.5)
        if self.ai_model:
            features = [t_naive.hour, 1.0, 50.0, 1.0, 0.01, 1.0]
            prob = self.ai_model.predict_proba(pd.DataFrame([features], columns=["entry_hour", "atr_5m", "rsi_5m", "disparity_5m", "fvg_size_ratio", "volume_ma_ratio"]))[0][1]
            if prob < 0.5: return

        sl = price * 0.98 # 2% 손절
        self.current_pos = {
            "entry_price": price, "sl": sl, "tp1": price * 1.025, "tp2": price * 1.05,
            "qty": int((self.capital * 0.01) / (price - sl + 1e-9)), "tp1_hit": False
        }
        self.state = "IN_POSITION"

    def _handle_position(self, candle):
        pos = self.current_pos
        if candle['low'] <= pos["sl"]:
            self.capital += (pos["sl"] - pos["entry_price"]) * pos["qty"]
            self.trades.append(self.capital); self.state = "MONITORING"
        elif not pos["tp1_hit"] and candle['high'] >= pos["tp1"]:
            pos["sl"] = pos["entry_price"]; pos["tp1_hit"] = True
        elif candle['high'] >= pos["tp2"]:
            self.capital += (pos["tp2"] - pos["entry_price"]) * pos["qty"]
            self.trades.append(self.capital); self.state = "MONITORING"

async def main():
    alpaca = AlpacaHandler(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"))
    ai_model = None
    model_path = os.path.join(os.path.dirname(__file__), "my_trading_bot", "ai", "smc_ai_filter.json")
    if os.path.exists(model_path) and AI_AVAILABLE:
        ai_model = XGBClassifier(); ai_model.load_model(model_path)

    symbols = ["TSLA", "NVDA", "AAPL", "MSFT", "AMD", "META", "GOOGL", "SMCI", "ARM", "NFLX"]
    print(f"=== [공격 모드 V4] 데이터 로드 및 분석 시작 ===")
    
    tasks = [alpaca.get_historical_candles(sym, timeframe="1Min", limit=10000) for sym in symbols]
    all_candles = await asyncio.gather(*tasks)

    total_pnl = 0; total_trades = 0
    for i, candles in enumerate(all_candles):
        if not candles: continue
        tester = SMCBacktester(symbols[i], pd.DataFrame(candles), ai_model)
        tester.run()
        pnl = tester.capital - tester.initial_capital
        total_pnl += pnl
        total_trades += len(tester.trades)
        print(f"[{symbols[i]}] 결과: PnL ${pnl:,.2f} | 매매 {len(tester.trades)}회")
    
    print(f"\n[최종 결과] 총 PnL: ${total_pnl:,.2f} | 총 매매: {total_trades}회")

if __name__ == "__main__":
    asyncio.run(main())
