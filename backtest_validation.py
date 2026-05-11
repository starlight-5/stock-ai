# -*- coding: utf-8 -*-
"""
과거 데이터를 사용하여 학습된 AI 모델의 실질적인 성능을 검증(Validation)하는 스크립트입니다.
국내(KR) 및 해외(US) 주식을 모두 지원합니다.
"""

import asyncio
import os
import logging
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from dotenv import load_dotenv, find_dotenv

from my_trading_bot.core.api_handler import KISApiHandler
from my_trading_bot.core.scanner import RankScanner
from my_trading_bot.core.alpaca_handler import AlpacaHandler
from my_trading_bot.strategies.v1_smc.poi_detector import (
    detect_fvg, detect_ob, calculate_atr, is_price_in_poi
)
from my_trading_bot.strategies.v1_smc.sl_tp_calculator import (
    calc_sl_price, calc_tp1, calc_tp2, calc_qty
)
from my_trading_bot.strategies.v1_smc.params import (
    TRADE_RISK_RATIO, COMMISSION_RATE
)

try:
    from xgboost import XGBClassifier
    AI_AVAILABLE = True
except ImportError:
    AI_AVAILABLE = False

# 로깅 설정
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("Validation")

class SMCBacktester:
    def __init__(self, symbol, candles, initial_capital=10000000.0, ai_model=None, market_type="KR"):
        self.symbol = symbol
        self.df = pd.DataFrame(candles)
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.trades = []
        self.state = "MONITORING"
        self.active_poi = None
        self.current_pos = None
        self.ai_model = ai_model
        self.market_type = market_type
        
        # 지표 사전 계산
        self.df = self._preprocess(self.df)

    def _resample_to_5m(self, df_1m):
        df_1m['time'] = pd.to_datetime(df_1m['time'])
        df_1m.set_index('time', inplace=True)
        
        ohlc_dict = {
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }
        df_5m = df_1m.resample('5min').apply(ohlc_dict).dropna()
        df_5m.reset_index(inplace=True)
        return df_5m.to_dict('records')

    def _preprocess(self, df):
        # RSI (14) 계산
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / (loss + 1e-9)
        df['rsi_5m'] = 100 - (100 / (1 + rs))
        
        # 이동평균선 (20일선) 및 이격도
        df['ma20_5m'] = df['close'].rolling(window=20).mean()
        df['disparity_5m'] = df['close'] / df['ma20_5m']
        return df

    def run(self):
        start_idx = 250
        if len(self.df) < start_idx + 10:
            return

        for i in range(start_idx, len(self.df)):
            current_candle = self.df.iloc[i]
            price = current_candle['close']
            curr_dt = pd.to_datetime(current_candle['time'])
            
            if self.state == "MONITORING":
                if curr_dt.minute % 5 == 0:
                    past_df = self.df.iloc[:i]
                    candles_5m = self._resample_to_5m(past_df.iloc[-250:]) 
                    atr = calculate_atr(candles_5m)
                    fvg = detect_fvg(candles_5m, atr=atr)
                    ob = detect_ob(candles_5m, fvg)
                    poi_zones = [z for z in (fvg + ob) if z["type"] == "bullish"]
                    self.active_poi_candidates = poi_zones
                
                touched = is_price_in_poi(price, getattr(self, 'active_poi_candidates', []))
                if touched:
                    self.state = "STANDBY"
                    self.active_poi = touched
            
            elif self.state == "STANDBY":
                high = current_candle['high']
                
                # CHoCH (상승 반전) 체크: 최근 3봉 고가 돌파
                lookback = self.df.iloc[i-3:i]
                if high > lookback['high'].max():
                    # 진입 결정
                    past_df_entry = self.df.iloc[:i+1]
                    candles_1m = past_df_entry.iloc[-20:].to_dict('records')
                    candles_5m = self._resample_to_5m(past_df_entry.iloc[-250:])
                    
                    sl = calc_sl_price(candles_1m, "long")
                    if not sl or sl >= price:
                        sl = price * 0.995 
                    
                    # 수수료 필터 (국내 0.5% 왕복 가정)
                    risk_pct = (price - sl) / price
                    if risk_pct < 0.008:
                        self.state = "MONITORING"
                        continue

                    tp1 = calc_tp1(price, sl)
                    fvg_poi = detect_fvg(candles_5m)
                    bearish_fvgs = [z for z in fvg_poi if z["type"] == "bearish"]
                    tp2 = calc_tp2(price, sl, candles_5m, bearish_fvgs)
                    
                    qty = calc_qty(self.capital, TRADE_RISK_RATIO, price, sl)
                    
                    if qty > 0:
                        self.current_pos = {
                            "entry_price": price,
                            "sl": sl,
                            "initial_sl": sl,
                            "tp1": tp1,
                            "tp2": tp2,
                            "qty": qty,
                            "tp1_hit": False,
                            "time": current_candle['time']
                        }
                        self.state = "IN_POSITION"
            
            elif self.state == "IN_POSITION":
                self._update_position(current_candle)

    def _update_position(self, candle):
        pos = self.current_pos
        high, low = candle['high'], candle['low']
        
        if low <= pos["sl"]:
            fee = pos["entry_price"] * pos["qty"] * COMMISSION_RATE
            pnl = (pos["sl"] - pos["entry_price"]) * pos["qty"] - fee
            self.capital += pnl
            self.trades.append({"type": "SL", "pnl": pnl})
            self._reset_state()
            return

        r_dist = pos["entry_price"] - pos["initial_sl"]
        if not pos["tp1_hit"] and high >= (pos["entry_price"] + r_dist * 2.0):
            pos["sl"] = pos["entry_price"] + r_dist * 1.0 
            pos["tp1_hit"] = True 
            return

        if high >= pos["tp2"]:
            rem_qty = pos["qty"]
            fee = pos["tp2"] * rem_qty * COMMISSION_RATE
            pnl = (pos["tp2"] - pos["entry_price"]) * rem_qty - fee
            self.capital += pnl
            self.trades.append({"type": "TP2", "pnl": pnl})
            self._reset_state()

    def _reset_state(self):
        self.state = "MONITORING"
        self.active_poi = None
        self.current_pos = None

async def main():
    load_dotenv(find_dotenv())
    market_type = os.getenv("MARKET_TYPE", "KR") # .env 또는 환경변수로 선택
    
    api = KISApiHandler(os.getenv("KIS_APP_KEY"), os.getenv("KIS_APP_SECRET"), os.getenv("KIS_ENV", "demo"))
    api.issue_access_token()
    alpaca = AlpacaHandler(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"))
    
    # AI 모델 로드 생략 (필요 시 XGBClassifier 추가)
    
    if market_type == "KR":
        symbols = ["005930", "000660", "035720", "035420"] # 삼성전자, SK하이닉스, 카카오, 네이버
        print(f"=== [KR] 국내 주식 검증 시작 ===")
    else:
        symbols = ["TSLA", "NVDA", "AAPL", "MSFT"]
        print(f"=== [US] 해외 주식 검증 시작 ===")
        
    results = []
    for symbol in symbols:
        all_candles = []
        if market_type == "US":
            end_dt = datetime.now() - timedelta(days=1)
            start_dt = end_dt - timedelta(days=5) # 샘플로 5일치
            start_str = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            all_candles = await alpaca.get_historical_candles(symbol, timeframe="1Min", limit=10000, start=start_str)
        else:
            # KIS 국내 분봉 조회 (최근 데이터 100건 예시)
            res = api.get_domestic_minute_chart(symbol, "153000", datetime.now().strftime("%Y%m%d"))
            if res.get("output2"):
                # KIS 응답을 공통 포맷으로 변환
                for c in reversed(res["output2"]):
                    all_candles.append({
                        "time": f"{c['stck_bsop_date']} {c['stck_cntg_hour'][:2]}:{c['stck_cntg_hour'][2:4]}",
                        "open": float(c["stck_oprc"]),
                        "high": float(c["stck_hgpr"]),
                        "low": float(c["stck_lwpr"]),
                        "close": float(c["stck_prpr"]),
                        "volume": float(c["cntg_vol"])
                    })
        
        if not all_candles: 
            print(f"[{symbol}] 데이터를 가져오지 못했습니다.")
            continue
        
        tester = SMCBacktester(symbol, all_candles, market_type=market_type)
        tester.run()
        pnl = tester.capital - tester.initial_capital
        total = len(tester.trades)
        
        print(f"[{symbol}] PnL: {pnl:,.0f}, Trades: {total}")
        results.append({"symbol": symbol, "trades": total, "pnl": pnl})

    print("\n" + "="*60)
    print(f"   VALIDATION SUMMARY ({market_type})")
    print("="*60)
    for r in results:
        print(f"{r['symbol']:<10} | {r['trades']:<7} | {r['pnl']:>15,.0f}")
    print("="*60)

if __name__ == "__main__":
    asyncio.run(main())
