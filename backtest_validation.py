# -*- coding: utf-8 -*-
"""
[AI 모델 검증 전용] backtest_validation.py
학습된 AI 모델(smc_ai_filter.json)의 성능을 동일한 종목(TSLA, NVDA 등)에서 
AI 필터 적용 전/후로 비교 검증하기 위한 스크립트입니다.
"""

import asyncio
import os
import logging
from datetime import datetime, timedelta
import pandas as pd
from dotenv import load_dotenv, find_dotenv

from my_trading_bot.core.api_handler import KISApiHandler
from my_trading_bot.core.scanner import RankScanner
from my_trading_bot.core.alpaca_handler import AlpacaHandler
from my_trading_bot.strategies.v1_smc.poi_detector import (
    detect_fvg, detect_ob, calculate_atr, is_price_in_poi, is_overlapping
)
from my_trading_bot.strategies.v1_smc.sl_tp_calculator import (
    calc_sl_price, calc_tp1, calc_tp2
)
from my_trading_bot.strategies.v1_smc.params import (
    POI_CANDLE_COUNT, ENTRY_CANDLE_COUNT, ATR_PERIOD,
    TP1_RR_RATIO, TRADE_RISK_RATIO, COMMISSION_RATE, AI_PROB_THRESHOLD,
    TP1_CLOSE_RATIO
)

try:
    from xgboost import XGBClassifier
    AI_AVAILABLE = True
except ImportError:
    AI_AVAILABLE = False

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# [검증 설정] 
# False: AI가 거절하면 실제로 진입하지 않음 (실전 성능 테스트)
# True: 모든 타점에 진입하되 AI의 예측값만 기록 (데이터 수집용)
COLLECT_DATA_MODE = False 

load_dotenv(find_dotenv())

class SMCBacktester:
    def __init__(self, symbol: str, candles_1m: list, initial_capital: float = 10000000.0, ai_model=None):
        self.symbol = symbol
        self.df = pd.DataFrame(candles_1m)
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.ai_model = ai_model 
        self.market_type = "US"
        
        self.trades = []
        self.state = "MONITORING"
        self.active_poi = None
        self.poi_zones_entry = []
        self.current_pos = None
        self.data_rows = []
        self.filtered_count = 0
        
    def _resample_to_5m(self, df_1m: pd.DataFrame) -> list:
        df_1m['time'] = pd.to_datetime(df_1m['time'])
        df_1m.set_index('time', inplace=True)
        df_5m = df_1m.resample('5min').agg({
            'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
        }).dropna()
        df_1m.reset_index(inplace=True)
        return df_5m.reset_index().to_dict('records')

    def _calculate_indicators(self, df: pd.DataFrame):
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
        if len(self.df) < start_idx + 10: return

        for i in range(start_idx, len(self.df)):
            current_candle = self.df.iloc[i]
            price = current_candle['close']
            curr_time = current_candle['time']
            curr_dt = pd.to_datetime(curr_time, utc=True)
            
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
                    self._update_entry_poi(past_df.iloc[-20:]) 

            elif self.state == "STANDBY":
                if price > self.active_poi["high"] * 1.01:
                    self.state = "MONITORING"
                    continue
                
                past_df = self.df.iloc[:i]
                self._update_entry_poi(past_df.iloc[-20:])
                if is_price_in_poi(price, self.poi_zones_entry):
                    self._execute_entry(price, past_df, curr_time)

            elif self.state == "IN_POSITION":
                self._handle_position(current_candle)

    def _update_entry_poi(self, past_1m_candles_df: pd.DataFrame):
        candles_entry = past_1m_candles_df.to_dict('records')
        atr = calculate_atr(candles_entry)
        fvg = detect_fvg(candles_entry, atr=atr)
        ob = detect_ob(candles_entry, fvg)
        all_bullish_entry = [z for z in (fvg + ob) if z["type"] == "bullish"]
        self.poi_zones_entry = [z for z in all_bullish_entry if is_overlapping(self.active_poi, z)]

    def _is_uptrend(self, past_df: pd.DataFrame) -> bool:
        try:
            df_tmp = past_df.copy()
            df_tmp['time'] = pd.to_datetime(df_tmp['time'])
            df_15m = df_tmp.set_index('time').resample('15min').agg({
                'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'
            }).dropna()

            if len(df_15m) < 60: return False
            closes_15m = df_15m['close'].values
            ema50 = pd.Series(closes_15m).ewm(span=50, adjust=False).mean().iloc[-1]
            current_price = closes_15m[-1]
            ema_uptrend = current_price > ema50

            highs_15m, lows_15m = df_15m['high'].values, df_15m['low'].values
            prev_high, curr_high = highs_15m[-10:-5].max(), highs_15m[-5:].max()
            prev_low, curr_low = lows_15m[-10:-5].min(), lows_15m[-5:].min()
            structure_uptrend = (curr_high > prev_high) and (curr_low > prev_low)
            return ema_uptrend and structure_uptrend
        except: return False

    def _is_kill_zone(self, curr_dt: pd.Timestamp) -> bool:
        et_dt = curr_dt.tz_convert('America/New_York')
        h, m = et_dt.hour, et_dt.minute
        return (h == 9 and m >= 30) or (h == 10 and m < 30)

    def _execute_entry(self, price, past_df, curr_time):
        curr_dt = pd.to_datetime(curr_time, utc=True)
        if self._is_kill_zone(curr_dt): return
        if not self._is_uptrend(past_df): return

        candles_5m = self._resample_to_5m(past_df.iloc[-250:])
        candles_1m = past_df.iloc[-20:].to_dict('records')
        sl = calc_sl_price(candles_1m, direction="long")
        if not sl or sl >= price: sl = price * 0.988
        if (price - sl) / price < 0.008: return

        tp1 = calc_tp1(price, sl)
        bearish_fvgs = [z for z in detect_fvg(candles_5m) if z["type"] == "bearish"]
        tp2 = calc_tp2(price, sl, candles_5m, bearish_fvgs)
        
        risk_amt = self.capital * TRADE_RISK_RATIO
        qty = int(risk_amt / (price - sl))
        if qty <= 0: qty = 1
        
        df_5m = self._calculate_indicators(pd.DataFrame(self._resample_to_5m(past_df.iloc[-500:])))
        last_5m = df_5m.iloc[-1]
        features = {
            "entry_hour": pd.to_datetime(curr_time).hour,
            "atr_5m": calculate_atr(df_5m.to_dict('records')),
            "rsi_5m": last_5m['rsi_5m'], "disparity_5m": last_5m['disparity_5m'],
            "fvg_size_ratio": abs(price - sl) / price,
            "volume_ma_ratio": past_df.iloc[-1]['volume'] / past_df.iloc[-20:]['volume'].mean() if past_df.iloc[-20:]['volume'].mean() > 0 else 1.0
        }

        if self.ai_model:
            feature_cols = ["entry_hour", "atr_5m", "rsi_5m", "disparity_5m", "fvg_size_ratio", "volume_ma_ratio"]
            input_data = pd.DataFrame([[features[c] for c in feature_cols]], columns=feature_cols)
            prob = self.ai_model.predict_proba(input_data)[0][1]
            if prob < AI_PROB_THRESHOLD:
                self.filtered_count += 1
                if not COLLECT_DATA_MODE: return
                logger.info(f"  [AI FILTER-SKIP] 진입 거절 (확률: {prob:.2f})")

        self.current_pos = {
            "entry_price": price, "sl": sl, "initial_sl": sl, "tp1": tp1, "tp2": tp2,
            "qty": qty, "remaining_qty": qty, "tp1_hit": False, "entry_time": curr_time, "features": features
        }
        self.state = "IN_POSITION"
        logger.info(f"  [ENTRY] {curr_time} | Price: {price:.2f}, SL: {sl:.2f}, TP1: {tp1:.2f}, TP2: {tp2:.2f}")

    def _handle_position(self, candle):
        pos = self.current_pos
        high, low, curr_time = candle['high'], candle['low'], candle['time']

        if low <= pos["sl"]:
            rem_qty = pos["remaining_qty"]
            fee = pos["entry_price"] * rem_qty * COMMISSION_RATE
            pnl = (pos["sl"] - pos["entry_price"]) * rem_qty - fee
            self.capital += pnl
            self.trades.append({"type": "SL", "pnl": pnl})
            logger.info(f"  [EXIT-SL] {curr_time} | PnL: {pnl:.2f}, Balance: {self.capital:.2f}")
            self._reset_state()
            return

        if not pos["tp1_hit"] and high >= pos["tp1"]:
            close_qty = max(1, int(pos["remaining_qty"] * TP1_CLOSE_RATIO))
            fee = pos["entry_price"] * close_qty * COMMISSION_RATE
            pnl_partial = (pos["tp1"] - pos["entry_price"]) * close_qty - fee
            self.capital += pnl_partial
            pos["remaining_qty"] -= close_qty
            pos["sl"] = pos["entry_price"] * 1.002
            pos["tp1_hit"] = True
            logger.info(f"  [EXIT-TP1] {curr_time} | PnL: {pnl_partial:.2f}, SL Breakeven 이동")
            if pos["remaining_qty"] <= 0: self._reset_state()
            return

        if high >= pos["tp2"]:
            rem_qty = pos["remaining_qty"]
            fee = pos["entry_price"] * rem_qty * COMMISSION_RATE
            pnl = (pos["tp2"] - pos["entry_price"]) * rem_qty - fee
            self.capital += pnl
            self.trades.append({"type": "TP2", "pnl": pnl})
            logger.info(f"  [EXIT-TP2] {curr_time} | PnL: {pnl:.2f}, Balance: {self.capital:.2f}")
            self._reset_state()

    def _reset_state(self):
        self.state = "MONITORING"
        self.current_pos = None

async def main():
    load_dotenv(find_dotenv())
    api = KISApiHandler(os.getenv("KIS_APP_KEY"), os.getenv("KIS_APP_SECRET"))
    api.issue_access_token()
    alpaca = AlpacaHandler(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"))
    
    ai_model = None
    model_path = os.path.join(os.path.dirname(__file__), "my_trading_bot", "ai", "smc_ai_filter.json")
    if os.path.exists(model_path) and AI_AVAILABLE:
        ai_model = XGBClassifier()
        ai_model.load_model(model_path)
        logger.warning(f"AI 검증용 모델 로드 완료: {model_path}")

    # [검증 대상 종목 고정] 아까와 동일한 종목으로 비교
    top_symbols = [("NAS", "TSLA"), ("NAS", "NVDA"), ("NAS", "AAPL"), ("NAS", "MSFT"), ("NAS", "AMD")]
    results = []
    
    for excd, symbol in top_symbols:
        logger.info(f"=== {symbol} 검증 데이터 수집 중 ===")
        all_candles = []
        # 과거 60일치 데이터 수집
        start_dt = datetime.now() - timedelta(days=120)
        end_dt = datetime.now() - timedelta(days=60)
        for _ in range(3):
            candles = await alpaca.get_historical_candles(symbol, timeframe="1Min", limit=10000, start=start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"), end=end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"))
            if not candles: break
            all_candles.extend(candles)
            start_dt = datetime.strptime(candles[-1]["time"], "%Y-%m-%dT%H:%M:%SZ") + timedelta(minutes=1)
        
        if all_candles:
            tester = SMCBacktester(symbol, all_candles, ai_model=ai_model)
            tester.run()
            results.append({"symbol": symbol, "trades": len(tester.trades), "pnl": tester.capital - tester.initial_capital})

    print("\n" + "="*50)
    print("      AI MODEL VALIDATION SUMMARY")
    print("="*50)
    total_pnl = 0
    for r in results:
        print(f"{r['symbol']:<10} | {r['trades']:<8} | ${r['pnl']:>10.2f}")
        total_pnl += r["pnl"]
    print("-" * 50)
    print(f"TOTAL PnL: ${total_pnl:.2f}")
    print("="*50)

if __name__ == "__main__":
    asyncio.run(main())
