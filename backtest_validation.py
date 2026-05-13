# -*- coding: utf-8 -*-
"""
[공격 모드 V5] backtest_validation.py
과거 데이터 구간을 명시적으로 지정하여 데이터 부족 문제를 해결했습니다.
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
        self.all_bullish_pois = []

    def _prepare_5m_data(self, df_1m):
        df = df_1m.copy()
        df.set_index('time_naive', inplace=True)
        df_5m = df.resample('5min').agg({
            'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
        }).dropna()
        return df_5m.reset_index()

    def run(self):
        m5_list = self.df_5m.to_dict('records')
        # 윈도우를 50으로 확대하여 더 넓은 구조 분석
        for i in range(50, len(m5_list)):
            window = m5_list[i-50:i]
            # ATR 필터 없이 모든 지지선 수집
            fvg = detect_fvg(window, atr=0) 
            ob = detect_ob(window, fvg)
            bullish = [z for z in (fvg + ob) if z["type"] == "bullish"]
            for b in bullish:
                b["found_at"] = m5_list[i-1]['time_naive']
                self.all_bullish_pois.append(b)
        
        m1_data = self.df_1m.to_dict('records')
        for i in range(100, len(m1_data)):
            candle = m1_data[i]
            price = candle['close']
            t_naive = candle['time_naive']

            if self.state == "MONITORING":
                # 최근 4시간 이내 지지선 체크 (더 공격적)
                active_pois = [p for p in self.all_bullish_pois if t_naive - timedelta(hours=4) <= p["found_at"] < t_naive]
                for poi in active_pois:
                    if poi["low"] * 0.999 <= price <= poi["high"] * 1.001:
                        self._execute_entry(price, t_naive)
                        break
            elif self.state == "IN_POSITION":
                self._handle_position(candle)

    def _execute_entry(self, price, t_naive):
        # AI 필터 해제 (작동 여부 확인 우선)
        sl = price * 0.985 # 1.5% 손절
        self.current_pos = {
            "entry_price": price, "sl": sl, "tp1": price * 1.02, "tp2": price * 1.04,
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
    symbols = ["TSLA", "NVDA", "AAPL", "MSFT", "AMD", "META", "GOOGL", "SMCI", "ARM", "NFLX"]
    
    # 확실한 과거 데이터 구간 (최근 15일 전 ~ 3일 전)
    start_dt = (datetime.now() - timedelta(days=15)).strftime("%Y-%m-%dT%G:%i:%SZ") # 잘못된 포맷 방지
    start_str = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%dT09:30:00Z")
    end_str = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%dT16:00:00Z")

    print(f"=== [공격 모드 V5] {start_str} ~ {end_str} 데이터 로드 중... ===")
    
    tasks = [alpaca.get_historical_candles(sym, timeframe="1Min", limit=10000, start=start_str, end=end_str) for sym in symbols]
    all_candles = await asyncio.gather(*tasks)

    total_pnl = 0; total_trades = 0
    for i, candles in enumerate(all_candles):
        if not candles or len(candles) < 500: 
            print(f"[{symbols[i]}] 데이터 부족 ({len(candles) if candles else 0}개)"); continue
        
        print(f"[{symbols[i]}] 데이터 {len(candles)}개 로드 완료. 백테스트 시작...")
        tester = SMCBacktester(symbols[i], pd.DataFrame(candles))
        tester.run()
        pnl = tester.capital - tester.initial_capital
        total_pnl += pnl
        total_trades += len(tester.trades)
        print(f"[{symbols[i]}] 결과: PnL ${pnl:,.2f} | 매매 {len(tester.trades)}회")
    
    print(f"\n[최종 결과] 총 PnL: ${total_pnl:,.2f} | 총 매매: {total_trades}회")

if __name__ == "__main__":
    asyncio.run(main())
