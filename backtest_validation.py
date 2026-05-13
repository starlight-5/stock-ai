# -*- coding: utf-8 -*-
"""
[AI 전략 최적화 비교] backtest_validation.py
3가지 개선 아이디어의 7가지 조합을 모두 테스트하여 최적의 설정을 찾습니다.
1. AI 임계치 강화 (0.5 -> 0.7)
2. 손절가 여유 (0.8% -> 1.0%)
3. 변동성 폭탄 필터 (ATR 기반 장대봉 진입 금지)
"""

import asyncio
import os
import logging
from datetime import datetime, timedelta
import pandas as pd
from dotenv import load_dotenv, find_dotenv

from my_trading_bot.core.api_handler import KISApiHandler
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

# 로깅 설정 (비교를 위해 로그를 줄임)
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv(find_dotenv())

class SMCBacktester:
    def __init__(self, symbol, candles, ai_model=None, 
                 ai_threshold=0.5, min_risk_pct=0.008, vol_filter_on=False):
        self.symbol = symbol
        self.df = pd.DataFrame(candles)
        self.initial_capital = 10000000.0
        self.capital = self.initial_capital
        self.ai_model = ai_model
        
        # 최적화 파라미터
        self.ai_threshold = ai_threshold
        self.min_risk_pct = min_risk_pct
        self.vol_filter_on = vol_filter_on
        
        self.trades = []
        self.state = "MONITORING"
        self.active_poi = None
        self.poi_zones_entry = []
        self.current_pos = None
        self.filtered_count = 0
        
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
                # 진입용 1분봉 POI 계산
                candles_1m = past_df.iloc[-20:].to_dict('records')
                atr_1m = calculate_atr(candles_1m)
                fvg_1m = detect_fvg(candles_1m, atr=atr_1m)
                ob_1m = detect_ob(candles_1m, fvg_1m)
                entry_pois = [z for z in (fvg_1m + ob_1m) if z["type"] == "bullish" and is_overlapping(self.active_poi, z)]
                
                if is_price_in_poi(price, entry_pois):
                    self._execute_entry(price, past_df, current_candle)

            elif self.state == "IN_POSITION":
                self._handle_position(current_candle)

    def _execute_entry(self, price, past_df, candle):
        curr_time = candle['time']
        # 1. 킬존 필터
        et_dt = pd.to_datetime(curr_time, utc=True).tz_convert('America/New_York')
        if (et_dt.hour == 9 and et_dt.minute >= 30) or (et_dt.hour == 10 and et_dt.minute < 30): return

        # 2. HTF 추세 필터 (간소화)
        df_5m = pd.DataFrame(self._resample_to_5m(past_df.iloc[-500:]))
        ema50 = df_5m['close'].ewm(span=50).mean().iloc[-1]
        if price < ema50: return

        # 3. 변동성 폭탄 필터 (조합 옵션)
        if self.vol_filter_on:
            last_5m_body = abs(df_5m.iloc[-1]['close'] - df_5m.iloc[-1]['open'])
            atr_5m = calculate_atr(df_5m.to_dict('records'))
            if last_5m_body > atr_5m * 1.5: return

        # SL/TP 계산
        candles_1m = past_df.iloc[-20:].to_dict('records')
        sl = calc_sl_price(candles_1m, direction="long")
        if not sl or sl >= price: sl = price * 0.988
        
        # 리스크 필터 (조합 옵션)
        if (price - sl) / price < self.min_risk_pct: return

        # AI 필터 (조합 옵션)
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
            if prob < self.ai_threshold:
                self.filtered_count += 1
                return

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
            pos["sl"] = pos["entry_price"] * 1.002
            pos["tp1_hit"] = True
            if pos["remaining_qty"] <= 0: self.state = "MONITORING"
            return
        if high >= pos["tp2"]:
            pnl = (pos["tp2"] - pos["entry_price"]) * pos["remaining_qty"] - (pos["entry_price"] * pos["remaining_qty"] * COMMISSION_RATE)
            self.capital += pnl
            self.trades.append(pnl)
            self.state = "MONITORING"

async def main():
    api = KISApiHandler(os.getenv("KIS_APP_KEY"), os.getenv("KIS_APP_SECRET"))
    api.issue_access_token()
    alpaca = AlpacaHandler(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"))
    
    ai_model = None
    model_path = os.path.join(os.path.dirname(__file__), "my_trading_bot", "ai", "smc_ai_filter.json")
    if os.path.exists(model_path) and AI_AVAILABLE:
        ai_model = XGBClassifier(); ai_model.load_model(model_path)

    # 데이터 미리 로드 (시간 절약)
    symbols = ["TSLA", "NVDA", "AAPL", "MSFT", "AMD"]
    market_data = {}
    print(f"=== 검증용 데이터 {len(symbols)}종목 로드 중... ===")
    for sym in symbols:
        start_dt = datetime.now() - timedelta(days=100)
        end_dt = datetime.now() - timedelta(days=80)
        market_data[sym] = await alpaca.get_historical_candles(sym, timeframe="1Min", limit=10000, start=start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"), end=end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"))

    # 조합 정의
    configs = [
        {"name": "기본(Base)", "ai": 0.5, "sl": 0.008, "vol": False},
        {"name": "조합1(AI강화)", "ai": 0.7, "sl": 0.008, "vol": False},
        {"name": "조합2(손절여유)", "ai": 0.5, "sl": 0.010, "vol": False},
        {"name": "조합3(변동성필터)", "ai": 0.5, "sl": 0.008, "vol": True},
        {"name": "조합4(AI+손절)", "ai": 0.7, "sl": 0.010, "vol": False},
        {"name": "조합5(AI+변동성)", "ai": 0.7, "sl": 0.008, "vol": True},
        {"name": "조합6(손절+변동성)", "ai": 0.5, "sl": 0.010, "vol": True},
        {"name": "조합7(전체적용)", "ai": 0.7, "sl": 0.010, "vol": True},
    ]

    final_results = []
    print(f"\n=== 총 {len(configs)}가지 조합 테스트 시작 ===")
    
    for cfg in configs:
        total_pnl = 0
        total_trades = 0
        win_trades = 0
        
        for sym, candles in market_data.items():
            if not candles: continue
            tester = SMCBacktester(sym, candles, ai_model, cfg['ai'], cfg['sl'], cfg['vol'])
            tester.run()
            total_pnl += (tester.capital - tester.initial_capital)
            total_trades += len(tester.trades)
            win_trades += len([t for t in tester.trades if t > 0])
            
        win_rate = (win_trades / total_trades * 100) if total_trades > 0 else 0
        final_results.append({
            "name": cfg['name'],
            "pnl": total_pnl,
            "trades": total_trades,
            "win_rate": win_rate
        })
        print(f"[{cfg['name']}] 완료: PnL ${total_pnl:,.2f}, 승률 {win_rate:.2f}%")

    # 결과 표 출력
    print("\n" + "="*70)
    print(f"{'조합명':<15} | {'PnL (Total)':<15} | {'매매횟수':<8} | {'승률':<8}")
    print("-" * 70)
    for r in sorted(final_results, key=lambda x: x['pnl'], reverse=True):
        print(f"{r['name']:<15} | ${r['pnl']:>13,.2f} | {r['trades']:<8} | {r['win_rate']:>7.2f}%")
    print("="*70)

if __name__ == "__main__":
    asyncio.run(main())
