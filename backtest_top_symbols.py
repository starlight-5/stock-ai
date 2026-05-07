# -*- coding: utf-8 -*-
"""
상위 거래량/거래대금 종목들에 대해 6개월간의 1분봉 데이터를 사용하여 
SMC 전략(5m POI + 1m Entry)의 성과를 백테스트하는 스크립트입니다.
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
    TP1_RR_RATIO, TRADE_RISK_RATIO
)

try:
    from xgboost import XGBClassifier
    AI_AVAILABLE = True
except ImportError:
    AI_AVAILABLE = False

# 로깅 설정 (결론만 보기 위해 WARNING 레벨로 설정)
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv(find_dotenv())

class SMCBacktester:
    def __init__(self, symbol: str, candles_1m: list, initial_capital: float = 10000.0, ai_model=None):
        self.symbol = symbol
        self.df = pd.DataFrame(candles_1m)
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.ai_model = ai_model # 학습된 모델 추가
        
        self.trades = []
        self.state = "MONITORING" # MONITORING, STANDBY, IN_POSITION
        self.active_poi = None
        self.poi_zones_entry = []
        self.current_pos = None
        self.data_rows = [] # AI 학습용 데이터를 담을 리스트
        self.filtered_count = 0 # AI가 걸러낸 횟수
        
    def _resample_to_5m(self, df_1m: pd.DataFrame) -> list:
        """1분봉 데이터를 5분봉으로 리샘플링합니다."""
        df_1m['time'] = pd.to_datetime(df_1m['time'])
        df_1m.set_index('time', inplace=True)
        
        df_5m = df_1m.resample('5min').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).dropna()
        
        df_1m.reset_index(inplace=True)
        # KIS/Alpaca 호환 리스트 형식으로 변환
        return df_5m.reset_index().to_dict('records')

    def _calculate_indicators(self, df: pd.DataFrame):
        """AI 학습용 기술적 지표를 계산합니다."""
        # RSI 14
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['rsi_5m'] = 100 - (100 / (1 + rs))
        
        # 이동평균선 (20일선) 및 이격도
        df['ma20_5m'] = df['close'].rolling(window=20).mean()
        df['disparity_5m'] = df['close'] / df['ma20_5m']
        return df

    def run(self):
        logger.info(f"[{self.symbol}] 백테스트 시작 (데이터: {len(self.df)}개)")
        
        # 슬라이딩 윈도우 시뮬레이션
        # 1분봉 하나씩 읽으며 시뮬레이션
        # 최소 50개의 5분봉(250개의 1분봉)이 필요하므로 250번째부터 시작
        start_idx = 250
        if len(self.df) < start_idx + 10:
            logger.warning(f"[{self.symbol}] 데이터가 너무 적어 테스트를 건너뜁니다.")
            return

        for i in range(start_idx, len(self.df)):
            current_candle = self.df.iloc[i]
            price = current_candle['close']
            curr_time = current_candle['time']
            curr_dt = pd.to_datetime(curr_time)
            
            if self.state == "MONITORING":
                # 5분봉 POI 탐지는 5분 단위로만 갱신 (최적화)
                if curr_dt.minute % 5 == 0 or i == start_idx:
                    past_df = self.df.iloc[:i]
                    candles_5m = self._resample_to_5m(past_df.iloc[-250:]) 
                    
                    atr = calculate_atr(candles_5m)
                    fvg = detect_fvg(candles_5m, atr=atr)
                    ob = detect_ob(candles_5m, fvg)
                    poi_zones = [z for z in (fvg + ob) if z["type"] == "bullish"]
                    self.active_poi_candidates = poi_zones # 후보군 저장
                
                # 터치 여부는 매 분 확인
                touched = is_price_in_poi(price, getattr(self, 'active_poi_candidates', []))
                if touched:
                    self.state = "STANDBY"
                    self.active_poi = touched
                    # 1분봉 POI 탐지 (진입용)
                    self._update_entry_poi(past_df.iloc[-20:]) 

            elif self.state == "STANDBY":
                # 이탈 체크 (상단 1% 이탈 시 MONITORING 복귀)
                if price > self.active_poi["high"] * 1.01:
                    self.state = "MONITORING"
                    self.active_poi = None
                    continue
                
                # 주기적으로(여기선 매 분) 진입 POI 갱신
                past_df = self.df.iloc[:i]
                self._update_entry_poi(past_df.iloc[-20:])
                
                # 1분봉 POI 터치 여부 확인
                touched_entry = is_price_in_poi(price, self.poi_zones_entry)
                if touched_entry:
                    self._execute_entry(price, past_df, curr_time)

            elif self.state == "IN_POSITION":
                self._handle_position(current_candle)

        self._print_summary()

    def _update_entry_poi(self, past_1m_candles_df: pd.DataFrame):
        candles_entry = past_1m_candles_df.to_dict('records')
        atr = calculate_atr(candles_entry)
        fvg = detect_fvg(candles_entry, atr=atr)
        ob = detect_ob(candles_entry, fvg)
        
        all_bullish_entry = [z for z in (fvg + ob) if z["type"] == "bullish"]
        self.poi_zones_entry = [
            z for z in all_bullish_entry 
            if is_overlapping(self.active_poi, z)
        ]

    def _execute_entry(self, price, past_df, curr_time):
        # 5분봉 리샘플링하여 TP2 계산용으로 전달
        candles_5m = self._resample_to_5m(past_df.iloc[-250:])
        candles_1m = past_df.iloc[-20:].to_dict('records')
        
        sl = calc_sl_price(candles_1m, direction="long")
        if not sl or sl >= price:
            return

        tp1 = calc_tp1(price, sl)
        # Bearish FVG 탐지
        fvg_poi = detect_fvg(candles_5m)
        bearish_fvgs = [z for z in fvg_poi if z["type"] == "bearish"]
        tp2 = calc_tp2(price, sl, candles_5m, bearish_fvgs)
        
        # 수량 계산 (리스크 2%)
        risk_amt = self.capital * TRADE_RISK_RATIO
        qty = int(risk_amt / (price - sl))
        if qty <= 0: qty = 1
        
        # [AI 피처 추출]
        # 5분봉 기준 지표 계산
        df_5m_raw = pd.DataFrame(self._resample_to_5m(past_df.iloc[-500:]))
        df_5m = self._calculate_indicators(df_5m_raw)
        last_5m = df_5m.iloc[-1]
        
        features = {
            "entry_hour": pd.to_datetime(curr_time).hour,
            "atr_5m": calculate_atr(df_5m.to_dict('records')),
            "rsi_5m": last_5m['rsi_5m'] if not pd.isna(last_5m['rsi_5m']) else 50,
            "disparity_5m": last_5m['disparity_5m'] if not pd.isna(last_5m['disparity_5m']) else 1.0,
            "fvg_size_ratio": abs(price - sl) / price,
            "volume_ma_ratio": past_df.iloc[-1]['volume'] / past_df.iloc[-20:]['volume'].mean() if past_df.iloc[-20:]['volume'].mean() > 0 else 1.0
        }

        # [AI 필터링]
        if self.ai_model is not None:
            # 피처 순서 맞추기
            feature_cols = ["entry_hour", "atr_5m", "rsi_5m", "disparity_5m", "fvg_size_ratio", "volume_ma_ratio"]
            input_data = pd.DataFrame([[features[c] for c in feature_cols]], columns=feature_cols)
            
            prob = self.ai_model.predict_proba(input_data)[0][1]
            if prob < 0.45: # 성공 확률 45% 미만은 필터링 (보수적 접근)
                self.filtered_count += 1
                return

        self.current_pos = {
            "entry_price": price,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "qty": qty,
            "tp1_hit": False,
            "entry_time": curr_time,
            "features": features # 피처 데이터 저장
        }
        self.state = "IN_POSITION"
        logger.info(f"  [ENTRY] {curr_time} | Price: {price:.2f}, SL: {sl:.2f}, TP1: {tp1:.2f}, TP2: {tp2:.2f}")

    def _handle_position(self, candle):
        pos = self.current_pos
        high = candle['high']
        low = candle['low']
        curr_time = candle['time']
        
        # 1. 손절 체크
        if low <= pos["sl"]:
            pnl = (pos["sl"] - pos["entry_price"]) * pos["qty"]
            # 만약 TP1이 이미 히트되었다면 절반만 손실 (실제로는 본절 이동 로직 등이 있을 수 있음)
            self.capital += pnl
            self.trades.append({"symbol": self.symbol, "type": "SL", "pnl": pnl, "time": curr_time})
            self._record_data(label=0) # [AI 라벨링] 실패(0)
            logger.info(f"  [EXIT-SL] {curr_time} | PnL: {pnl:.2f}, Balance: {self.capital:.2f}")
            self._reset_state()
            return

        # 2. 1차 익절 체크
        if not pos["tp1_hit"] and high >= pos["tp1"]:
            # 절반 매도
            pnl = (pos["tp1"] - pos["entry_price"]) * (pos["qty"] // 2)
            self.capital += pnl
            pos["tp1_hit"] = True
            # SL을 진입가로 이동 (Breakeven)
            pos["sl"] = pos["entry_price"]
            logger.info(f"  [TP1 HIT] {curr_time} | PnL: {pnl:.2f}, Moving SL to Entry")

        # 3. 2차 익절 체크
        if high >= pos["tp2"]:
            rem_qty = pos["qty"] - (pos["qty"] // 2 if pos["tp1_hit"] else 0)
            pnl = (pos["tp2"] - pos["entry_price"]) * rem_qty
            self.capital += pnl
            self.trades.append({"symbol": self.symbol, "type": "TP2", "pnl": pnl, "time": curr_time})
            self._record_data(label=1) # [AI 라벨링] 성공(1)
            logger.info(f"  [EXIT-TP2] {curr_time} | PnL: {pnl:.2f}, Balance: {self.capital:.2f}")
            self._reset_state()

    def _record_data(self, label: int):
        """매매 종료 시 피처와 라벨을 결합하여 리스트에 추가합니다."""
        if self.current_pos and "features" in self.current_pos:
            row = self.current_pos["features"]
            row["label"] = label
            row["symbol"] = self.symbol
            self.data_rows.append(row)

    def _reset_state(self):
        self.state = "MONITORING"
        self.current_pos = None
        self.active_poi = None
        self.poi_zones_entry = []

    def _print_summary(self):
        profit = self.capital - self.initial_capital
        win_count = len([t for t in self.trades if t["pnl"] > 0])
        total_trades = len(self.trades)
        win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0
        
        print(f"\n--- Backtest Summary: {self.symbol} ---")
        print(f"Total Trades: {total_trades}")
        print(f"Win Rate: {win_rate:.2f}%")
        print(f"Total PnL: ${profit:.2f}")
        print(f"Final Capital: ${self.capital:.2f}")
        if self.filtered_count > 0:
            print(f"AI Filtered: {self.filtered_count} trades")
        print("------------------------------------\n")

async def main():
    # 1. API 초기화
    app_key = os.getenv("KIS_APP_KEY")
    app_secret = os.getenv("KIS_APP_SECRET")
    alpaca_key = os.getenv("ALPACA_API_KEY")
    alpaca_secret = os.getenv("ALPACA_SECRET_KEY")
    
    api = KISApiHandler(app_key, app_secret)
    api.issue_access_token()
    
    # AI 모델 로드
    ai_model = None
    model_path = "smc_ai_filter.json"
    if os.path.exists(model_path) and AI_AVAILABLE:
        try:
            ai_model = XGBClassifier()
            ai_model.load_model(model_path)
            logger.warning(f"AI 모델 로드 성공: {model_path}")
        except Exception as e:
            logger.error(f"AI 모델 로드 실패: {e}")

    scanner = RankScanner(api)
    alpaca = AlpacaHandler(alpaca_key, alpaca_secret)
    
    # 2. 상위 종목 스캔 (KIS API 호출 제한 고려)
    try:
        top_symbols = scanner.get_top_symbols(limit=5)
    except Exception as e:
        logger.error(f"상위 종목 스캔 실패: {e}. 기본 종목(TSLA, NVDA, AAPL, MSFT, AMD)으로 대체합니다.")
        top_symbols = [("NAS", "TSLA"), ("NAS", "NVDA"), ("NAS", "AAPL"), ("NAS", "MSFT"), ("NAS", "AMD")]
    
    top_symbols = [
        ("NAS", "TSLA"), ("NAS", "NVDA"), ("NAS", "AAPL"), 
        ("NAS", "MSFT"), ("NAS", "AMZN"), ("NAS", "META"), ("NAS", "AMD")
    ]
    results = []
    
    # 3. 종목별 데이터 로드 및 백테스트 (최근 14일치)
    for excd, symbol in top_symbols:
        logger.info(f"=== {symbol} ({excd}) 데이터 수집 중 (14일) ===")
        
        all_candles = []
        start_dt = datetime.now() - timedelta(days=14)
        
        # 10,000개씩 조회하여 수집
        for _ in range(3):
            start_str = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            candles = await alpaca.get_historical_candles(symbol, timeframe="1Min", limit=10000, start=start_str)
            
            if not candles:
                break
                
            all_candles.extend(candles)
            # 마지막 캔들의 시간 다음부터 다시 조회하도록 시간 업데이트
            last_time_str = candles[-1]["time"]
            # Alpaca 시간 형식 파싱: '2024-05-01T09:00:00Z'
            try:
                last_dt = datetime.strptime(last_time_str, "%Y-%m-%dT%H:%M:%SZ")
                start_dt = last_dt + timedelta(minutes=1)
            except ValueError:
                # 가끔 밀리초 포함된 경우 대응
                last_dt = datetime.strptime(last_time_str[:19], "%Y-%m-%dT%H:%M:%S")
                start_dt = last_dt + timedelta(minutes=1)
                
            if len(candles) < 10000: # 더 이상 데이터 없음
                break
        
        if not all_candles:
            logger.warning(f"{symbol} 데이터를 가져오지 못했습니다.")
            continue
            
        logger.info(f"총 {len(all_candles)}개의 1분봉 데이터 수집 완료. 백테스트 시작...")
        tester = SMCBacktester(symbol, all_candles, ai_model=ai_model)
        tester.run()
        print(f"[{symbol}] 백테스트 완료: PnL ${tester.capital - tester.initial_capital:.2f}, 진입 {len(tester.trades)}회")
        results.append({
            "symbol": symbol,
            "trades": len(tester.trades),
            "pnl": tester.capital - tester.initial_capital,
            "data_rows": tester.data_rows
        })

    # 4. 전체 요약 출력
    print("\n" + "="*50)
    print("      SMC STRATEGY BACKTEST FINAL SUMMARY")
    print("="*50)
    print(f"{'Symbol':<10} | {'Trades':<8} | {'Total PnL':<12}")
    print("-" * 50)
    total_pnl = 0
    for r in results:
        print(f"{r['symbol']:<10} | {r['trades']:<8} | ${r['pnl']:>10.2f}")
        total_pnl += r["pnl"]
    print("-" * 50)
    print(f"TOTAL PnL: ${total_pnl:.2f}")
    print("="*50)

    # 5. AI 학습용 CSV 저장
    all_data = []
    for r in results:
        if "data_rows" in r:
            all_data.extend(r["data_rows"])
    
    if all_data:
        df_ai = pd.DataFrame(all_data)
        csv_path = "trading_data_for_ai.csv"
        df_ai.to_csv(csv_path, index=False)
        print(f"\n[AI Data Collection] {len(df_ai)}개의 매매 기록이 {csv_path}에 저장되었습니다.")

if __name__ == "__main__":
    asyncio.run(main())
