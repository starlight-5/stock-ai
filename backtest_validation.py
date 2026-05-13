# -*- coding: utf-8 -*-
"""
[공격적 수익 창출 모드] backtest_validation.py
필터를 최적화하여 매매 횟수를 늘리고 수익률을 극대화합니다.
1. AI 임계치 완화 (0.7 -> 0.6)
2. 추세 필터 제거 (하락 후 반등 변곡점 공략)
3. 종목 유니버스 확대 (5종목 -> 10종목)
"""

import asyncio
import os
import logging
from datetime import datetime, timedelta
import pandas as pd
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

logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')

load_dotenv(find_dotenv())

class SMCBacktester:
    def __init__(self, symbol, candles, ai_model=None, ai_threshold=0.6, min_risk_pct=0.008):
        self.symbol = symbol
        self.df = pd.DataFrame(candles)
        self.initial_capital = 10000000.0
        self.capital = self.initial_capital
        self.ai_model = ai_model
        self.ai_threshold = ai_threshold
        self.min_risk_pct = min_risk_pct
        
        self.trades = []
        self.state = "MONITORING"
        self.active_poi = None
        self.current_pos = None

    def _resample_to_5m(self, df_1m):
        df_1m['time'] = pd.to_datetime(df_1m['time'])
        df_1m.set_index('time', inplace=True)
        df_5m = df_1m.resample('5min').agg({
            'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
        }).dropna()
        df_1m.reset_index(inplace=True)
        return df_5m.reset_index().to_dict('records')

    def _calculate_indicators(self, df):
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / (loss + 1e-9)
        df['rsi_5m'] = 100 - (100 / (1 + rs))
        df['ma20_5m'] = df['close'].rolling(window=20).mean()
        df['disparity_5m'] = df['close'] / df['ma20_5m']
        return df

    def run(self):
        start_idx = 250
        if len(self.df) < start_idx: return

        for i in range(start_idx, len(self.df)):
            current_candle = self.df.iloc[i]
            price = current_candle['close']
            curr_dt = pd.to_datetime(current_candle['time'], utc=True)
            
            if self.state == "MONITORING":
                if curr_dt.minute % 5 == 0:
                    past_df = self.df.iloc[:i]
                    candles_5m = self._resample_to_5m(past_df.iloc[-250:]) 
                    atr = calculate_atr(candles_5m)
                    fvg = detect_fvg(candles_5m, atr=atr)
                    ob = detect_ob(candles_5m, fvg)
                    self.active_poi_candidates = [z for z in (fvg + ob) if z["type"] == "bullish"]
                
                touched = is_price_in_poi(price, getattr(self, 'active_poi_candidates', []))
                if touched:
                    self.state = "STANDBY"
                    self.active_poi = touched

            elif self.state == "STANDBY":
                if price > self.active_poi["high"] * 1.01:
                    self.state = "MONITORING"
                    continue
                
                past_df = self.df.iloc[:i]
                candles_1m = past_df.iloc[-20:].to_dict('records')
                atr_1m = calculate_atr(candles_1m)
                fvg_1m = detect_fvg(candles_1m, atr=atr_1m)
                ob_1m = detect_ob(candles_1m, fvg_1m)
                # 5m/1m POI 겹침 조건 완화 (단순히 1m POI만 있어도 진입 허용 고려)
                entry_pois = [z for z in (fvg_1m + ob_1m) if z["type"] == "bullish"]
                
                if is_price_in_poi(price, entry_pois):
                    self._execute_entry(price, past_df, current_candle)

            elif self.state == "IN_POSITION":
                self._handle_position(current_candle)

    def _execute_entry(self, price, past_df, candle):
        curr_time = candle['time']
        # 킬존 완화 (장 초반 30분만 피함)
        et_dt = pd.to_datetime(curr_time, utc=True).tz_convert('America/New_York')
        if (et_dt.hour == 9 and et_dt.minute < 30): return

        df_5m = pd.DataFrame(self._resample_to_5m(past_df.iloc[-500:]))
        # 추세 필터 제거 (역추세 반등 공략)

        # 피보나치 0.618로 완화 (더 많은 기회)
        leg_high = df_5m.iloc[-20:]['high'].max()
        leg_low = df_5m.iloc[-20:]['low'].min()
        fibo_618 = leg_high - (leg_high - leg_low) * 0.382 # 0.618 레벨
        if price > fibo_618: return

        candles_1m = past_df.iloc[-20:].to_dict('records')
        sl = calc_sl_price(candles_1m, direction="long")
        if not sl or sl >= price: sl = price * 0.99 
        
        # 리스크 최소화 (0.5%만 되어도 진입)
        if (price - sl) / price < 0.005: return

        # AI 필터 (0.6으로 완화)
        if self.ai_model:
            df_5m_ind = self._calculate_indicators(df_5m)
            last_5 = df_5m_ind.iloc[-1]
            features = [
                pd.to_datetime(curr_time).hour,
                calculate_atr(df_5m_ind.to_dict('records')),
                last_5['rsi_5m'], last_5['disparity_5m'],
                (price-sl)/price,
                candle['volume'] / past_df.iloc[-20:]['volume'].mean() if past_df.iloc[-20:]['volume'].mean() > 0 else 1.0
            ]
            prob = self.ai_model.predict_proba(pd.DataFrame([features], columns=["entry_hour", "atr_5m", "rsi_5m", "disparity_5m", "fvg_size_ratio", "volume_ma_ratio"]))[0][1]
            if prob < self.ai_threshold: return

        tp1 = calc_tp1(price, sl)
        tp2 = calc_tp2(price, sl, df_5m.to_dict('records'), [])
        
        risk_amt = self.capital * TRADE_RISK_RATIO
        qty = int(risk_amt / (price - sl))
        if qty <= 0: qty = 1
        
        self.current_pos = {
            "entry_price": price, "sl": sl, "tp1": tp1, "tp2": tp2,
            "qty": qty, "remaining_qty": qty, "tp1_hit": False
        }
        self.state = "IN_POSITION"

    def _handle_position(self, candle):
        pos = self.current_pos
        high, low = candle['high'], candle['low']
        if low <= pos["sl"]:
            pnl = (pos["sl"] - pos["entry_price"]) * pos["remaining_qty"] - (pos["entry_price"] * pos["remaining_qty"] * COMMISSION_RATE)
            self.capital += pnl
            self.trades.append(pnl)
            self.state = "MONITORING"
            return
        if not pos["tp1_hit"] and high >= pos["tp1"]:
            close_qty = int(pos["remaining_qty"] * TP1_CLOSE_RATIO)
            pnl = (pos["tp1"] - pos["entry_price"]) * close_qty - (pos["entry_price"] * close_qty * COMMISSION_RATE)
            self.capital += pnl
            self.trades.append(pnl)
            pos["remaining_qty"] -= close_qty
            pos["sl"] = pos["entry_price"] * 1.001 # 본절가 이동
            pos["tp1_hit"] = True
            if pos["remaining_qty"] <= 0: self.state = "MONITORING"
            return
        if high >= pos["tp2"]:
            pnl = (pos["tp2"] - pos["entry_price"]) * pos["remaining_qty"] - (pos["entry_price"] * pos["remaining_qty"] * COMMISSION_RATE)
            self.capital += pnl
            self.trades.append(pnl)
            self.state = "MONITORING"

async def main():
    alpaca = AlpacaHandler(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"))
    ai_model = None
    model_path = os.path.join(os.path.dirname(__file__), "my_trading_bot", "ai", "smc_ai_filter.json")
    if os.path.exists(model_path) and AI_AVAILABLE:
        ai_model = XGBClassifier(); ai_model.load_model(model_path)

    # 종목 리스트 10개로 확대
    symbols = ["TSLA", "NVDA", "AAPL", "MSFT", "AMD", "META", "GOOGL", "SMCI", "ARM", "NFLX"]
    market_data = {}
    print(f"=== [공격 모드] 데이터 {len(symbols)}종목 로드 중... ===")
    for sym in symbols:
        # 최근 30일 데이터로 테스트 (더 최신 트렌드 반영)
        start_dt = datetime.now() - timedelta(days=40)
        end_dt = datetime.now() - timedelta(days=10)
        market_data[sym] = await alpaca.get_historical_candles(sym, timeframe="1Min", limit=10000, start=start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"), end=end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"))

    # 공격 모드 설정 비교
    configs = [
        {"name": "현재(Safe)", "ai": 0.7, "risk": 0.008},
        {"name": "공격(Aggressive)", "ai": 0.6, "risk": 0.005},
    ]

    print(f"\n=== 수익 극대화 모드 테스트 시작 ===")
    for cfg in configs:
        total_pnl = 0; total_trades = 0; win_trades = 0
        for sym, candles in market_data.items():
            if not candles: continue
            tester = SMCBacktester(sym, candles, ai_model, cfg['ai'], cfg['risk'])
            tester.run()
            total_pnl += (tester.capital - tester.initial_capital)
            total_trades += len(tester.trades)
            win_trades += len([t for t in tester.trades if t > 0])
            
        win_rate = (win_trades / total_trades * 100) if total_trades > 0 else 0
        print(f"[{cfg['name']}] PnL: ${total_pnl:,.2f} | 매매: {total_trades}회 | 승률: {win_rate:.2f}%")

if __name__ == "__main__":
    asyncio.run(main())
