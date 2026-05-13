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
    TP1_RR_RATIO, TRADE_RISK_RATIO, COMMISSION_RATE, AI_PROB_THRESHOLD,
    TP1_CLOSE_RATIO
)

try:
    from xgboost import XGBClassifier
    AI_AVAILABLE = True
except ImportError:
    AI_AVAILABLE = False

# 로깅 설정 (진행 상황 확인을 위해 INFO 레벨로 설정)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# [설정] 데이터 수집 모드 (True일 경우 AI 필터가 걸러도 진입하여 결과를 기록합니다)
COLLECT_DATA_MODE = False

load_dotenv(find_dotenv())

class SMCBacktester:
    def __init__(self, symbol: str, candles_1m: list, initial_capital: float = 10000.0, ai_model=None):
        self.symbol = symbol
        self.df = pd.DataFrame(candles_1m)
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.ai_model = ai_model 
        self.market_type = getattr(self, 'market_type', "US") # 기본값
        
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

    def _is_uptrend(self, past_df: pd.DataFrame) -> bool:
        """
        [신규] HTF(Higher Time Frame) 추세 필터.
        SMC 핵심 원칙: 상위 타임프레임 추세 방향과 같은 방향으로만 진입합니다.
        하락 추세 중 FVG 롱 진입은 절대 금지 - 이것이 가장 많은 손절 원인이었습니다.

        [판단 로직 - 2가지 조건 모두 만족해야 상승 추세로 인정]
        1. EMA50 조건: 최근 15분봉 기준
           현재가(마지막 15분봉 종가) > EMA50 → 중기 상승 추세
        2. 가격 구조 조건: 최근 15분봉 10개 구간에서
           최근 5봉 고점 > 이전 5봉 고점(HH) AND 최근 5봉 저점 > 이전 5봉 저점(HL)
           → Higher High / Higher Low 구조 확인

        :param past_df: 현재 시점까지의 1분봉 DataFrame
        :return: True면 상승 추세 (진입 허용), False면 하락/횡보 (진입 차단)
        """
        try:
            # ── 15분봉 리샘플 (EMA / 구조 분석용) ──
            df_tmp = past_df.copy()
            df_tmp['time'] = pd.to_datetime(df_tmp['time'])
            df_tmp = df_tmp.set_index('time')
            df_15m = df_tmp.resample('15min').agg({
                'open': 'first', 'high': 'max',
                'low': 'min',   'close': 'last'
            }).dropna()

            # 최소 60개 15분봉(약 15시간)이 없으면 추세 판단 불가 → 보수적으로 차단
            if len(df_15m) < 60:
                return False

            closes_15m = df_15m['close'].values

            # ── 조건 1: EMA50 기준 현재가 위치 ──
            # EMA50: 최근 50개 15분봉 종가의 지수이동평균
            ema50 = pd.Series(closes_15m).ewm(span=50, adjust=False).mean().iloc[-1]
            current_price = closes_15m[-1]
            ema_uptrend = current_price > ema50

            # ── 조건 2: Higher High / Higher Low 가격 구조 ──
            # 최근 10개 15분봉을 전반/후반 5개로 나눠 구조 비교
            highs_15m = df_15m['high'].values
            lows_15m  = df_15m['low'].values

            prev_high = highs_15m[-10:-5].max()   # 이전 5봉 최고점
            curr_high = highs_15m[-5:].max()      # 최근 5봉 최고점
            prev_low  = lows_15m[-10:-5].min()    # 이전 5봉 최저점
            curr_low  = lows_15m[-5:].min()       # 최근 5봉 최저점

            # HH(Higher High): 최근 고점이 이전 고점보다 높음
            # HL(Higher Low):  최근 저점이 이전 저점보다 높음
            structure_uptrend = (curr_high > prev_high) and (curr_low > prev_low)

            result = ema_uptrend and structure_uptrend
            if not result:
                logger.debug(
                    f"[추세 필터 차단] EMA50={ema50:.2f} vs Price={current_price:.2f} "
                    f"| EMA_UP={ema_uptrend}, HH/HL={structure_uptrend}"
                )
            return result

        except Exception as e:
            logger.debug(f"[추세 필터] 계산 오류: {e}")
            return False  # 오류 시 보수적으로 진입 차단

    def _is_kill_zone(self, curr_dt: pd.Timestamp) -> bool:
        """
        [동기화] 미국 개장 직후 킬 존(Kill Zone) 여부를 판단합니다. (ET 기준)
        백테스트 데이터(UTC)를 ET로 변환하여 서머타임을 자동으로 처리합니다.
        """
        # UTC 시간을 미국 동부 시간(ET)으로 변환
        et_dt = curr_dt.tz_convert('America/New_York')
        h = et_dt.hour
        m = et_dt.minute

        # ET 기준 09:30 ~ 10:30 구간 차단
        is_after_start = (h > 9) or (h == 9 and m >= 30)
        is_before_end = (h < 10) or (h == 10 and m < 30)

        return is_after_start and is_before_end

    def _execute_entry(self, price, past_df, curr_time):
        # [개선 ①] 킬존(Kill Zone) 시간대 진입 차단
        curr_dt = pd.to_datetime(curr_time, utc=True)
        if self._is_kill_zone(curr_dt):
            return

        # [신규 ②] HTF 추세 필터: 상승 추세가 아니면 롱 진입 금지
        # SMC 핵심 원칙 - 상위 타임프레임(15분봉) 추세와 같은 방향으로만 진입
        if not self._is_uptrend(past_df):
            return

        # 5분봉 리샘플링하여 TP2 계산용으로 전달
        candles_5m = self._resample_to_5m(past_df.iloc[-250:])
        candles_1m = past_df.iloc[-20:].to_dict('records')

        sl = calc_sl_price(candles_1m, direction="long")
        if not sl or sl >= price:
            sl = price * 0.988  # 구조적 SL 실패 시 1.2% 기본 여유

        # [동기화] 최소 리스크 거리 필터 (0.8% 이상만 진입)
        risk_pct = (price - sl) / price
        if risk_pct < 0.008:
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
            if prob < AI_PROB_THRESHOLD: # params.py에 설정된 임계치 사용
                self.filtered_count += 1
                if not COLLECT_DATA_MODE:
                    return
                else:
                    logger.info(f"  [AI FILTER-SKIP] 데이터 수집을 위해 필터링 무시 후 진입 (확률: {prob:.2f})")

        self.current_pos = {
            "entry_price": price,
            "sl": sl,
            "initial_sl": sl,       # 트레일링/Breakeven 기준 SL
            "tp1": tp1,
            "tp2": tp2,
            "qty": qty,
            "remaining_qty": qty,   # [개선] 분할 익절 후 남은 수량 추적
            "tp1_hit": False,        # TP1 도달 여부 (Breakeven 이동 트리거)
            "entry_time": curr_time,
            "features": features
        }
        self.state = "IN_POSITION"
        logger.info(f"  [ENTRY] {curr_time} | Price: {price:.2f}, SL: {sl:.2f}, TP1: {tp1:.2f}, TP2: {tp2:.2f}")

    def _handle_position(self, candle):
        """
        [개선] 포지션 관리 로직:
          1. 손절(SL) 체크
          2. TP1 도달 → 50% 분할 익절 + SL을 Breakeven(진입가)으로 이동
          3. TP2 도달 → 나머지 전량 익절
        """
        pos = self.current_pos
        high = candle['high']
        low  = candle['low']
        curr_time = candle['time']

        # ── 1. 손절(SL) 체크 ──
        if low <= pos["sl"]:
            rem_qty = pos["remaining_qty"]
            fee = pos["entry_price"] * rem_qty * COMMISSION_RATE
            pnl = (pos["sl"] - pos["entry_price"]) * rem_qty - fee

            self.capital += pnl
            self.trades.append({"symbol": self.symbol, "type": "SL", "pnl": pnl, "time": curr_time})
            self._record_data(label=0)  # AI 라벨: 실패
            logger.info(f"  [EXIT-SL] {curr_time} | PnL: {pnl:.2f}, Balance: {self.capital:.2f}")
            self._reset_state()
            return

        # ── 2. TP1 도달 → 분할 익절 + Breakeven 이동 ──
        if not pos["tp1_hit"] and high >= pos["tp1"]:
            # [개선] TP1_CLOSE_RATIO(50%) 만큼 분할 익절
            close_qty = max(1, int(pos["remaining_qty"] * TP1_CLOSE_RATIO))
            fee = pos["entry_price"] * close_qty * COMMISSION_RATE
            pnl_partial = (pos["tp1"] - pos["entry_price"]) * close_qty - fee

            self.capital += pnl_partial
            pos["remaining_qty"] -= close_qty
            self.trades.append({"symbol": self.symbol, "type": "TP1", "pnl": pnl_partial, "time": curr_time})
            logger.info(f"  [EXIT-TP1 PARTIAL] {curr_time} | Qty: {close_qty}, PnL: {pnl_partial:.2f}")

            # [동기화] Breakeven: 나머지 물량의 SL을 진입가 + 0.2%로 이동 (수수료 방어)
            pos["sl"] = pos["entry_price"] * 1.002
            pos["tp1_hit"] = True
            logger.info(f"  [BREAKEVEN] SL 상향({pos['sl']:.2f})으로 이동 완료 (수수료 방어)")

            # 나머지 수량이 없으면 포지션 종료
            if pos["remaining_qty"] <= 0:
                self._record_data(label=1)
                self._reset_state()
            return

        # ── 3. TP2 도달 → 나머지 전량 익절 ──
        if high >= pos["tp2"]:
            rem_qty = pos["remaining_qty"]
            fee = pos["entry_price"] * rem_qty * COMMISSION_RATE
            pnl = (pos["tp2"] - pos["entry_price"]) * rem_qty - fee

            self.capital += pnl
            self.trades.append({"symbol": self.symbol, "type": "TP2", "pnl": pnl, "time": curr_time})
            self._record_data(label=1)  # AI 라벨: 성공
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
        total_pnl = self.capital - self.initial_capital
        win_trades = [t for t in self.trades if t['pnl'] > 0]
        sl_count = len([t for t in self.trades if t['type'] == "SL"])
        tp1_count = len([t for t in self.trades if t['type'] == "TP1"])
        tp2_count = len([t for t in self.trades if t['type'] == "TP2"])
        
        total_finished = sl_count + tp2_count
        win_rate = (tp2_count / total_finished * 100) if total_finished > 0 else 0
        
        print(f"\n--- [{self.symbol}] 테스트 결과 ---")
        print(f"최종 자본: ${self.capital:.2f} (수익: ${total_pnl:.2f})")
        print(f"매매 횟수: {len(self.trades)} (SL: {sl_count}, TP1: {tp1_count}, TP2: {tp2_count})")
        print(f"승률(TP2 기준): {win_rate:.2f}%")
        print(f"AI 필터링 횟수: {self.filtered_count}")
        print("------------------------------")

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
    # my_trading_bot/ai/ 폴더 내부의 모델 참조
    base_dir = os.path.dirname(os.path.abspath(__file__))
    model_path = os.path.join(base_dir, "my_trading_bot", "ai", "smc_ai_filter.json")
    if os.path.exists(model_path) and AI_AVAILABLE:
        try:
            ai_model = XGBClassifier()
            ai_model.load_model(model_path)
            logger.warning(f"AI 모델 로드 성공: {model_path}")
        except Exception as e:
            logger.error(f"AI 모델 로드 실패: {e}")

    scanner = RankScanner(api)
    alpaca = AlpacaHandler(alpaca_key, alpaca_secret)
    
    # 2. 상위 종목 스캔
    market_type = os.getenv("MARKET_TYPE", "KR")
    try:
        top_symbols_data = scanner.get_top_symbols(market_type=market_type, limit=10)
        top_symbols = [(excd, sym) for excd, sym in top_symbols_data]
        if not top_symbols:
            raise ValueError("스캐너가 종목을 반환하지 않았습니다.")
    except Exception as e:
        if market_type == "KR":
            top_symbols = [("J", "005930"), ("J", "000660"), ("J", "035720"), ("J", "035420")]
        else:
            top_symbols = [("NAS", "TSLA"), ("NAS", "NVDA"), ("NAS", "AAPL"), ("NAS", "MSFT"), ("NAS", "AMD")]
        logger.error(f"상위 종목 스캔 실패: {e}. 기본 종목으로 대체합니다: {top_symbols}")
    
    results = []
    
    # 3. 종목별 데이터 로드 및 백테스트
    for excd, symbol in top_symbols:
        logger.info(f"=== {symbol} ({excd}) 데이터 수집 중 ({market_type}) ===")
        
        all_candles = []
        if market_type == "US":
            start_dt = datetime.now() - timedelta(days=160)
            end_dt = datetime.now() - timedelta(days=80)
            end_str = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            # 10,000개씩 조회하여 수집 (최대 5번 = 50,000분)
            for _ in range(5):
                start_str = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                candles = await alpaca.get_historical_candles(symbol, timeframe="1Min", limit=10000, start=start_str, end=end_str)
                if not candles: break
                all_candles.extend(candles)
                try:
                    last_dt = datetime.strptime(candles[-1]["time"], "%Y-%m-%dT%H:%M:%SZ")
                    start_dt = last_dt + timedelta(minutes=1)
                except: break
                if len(candles) < 10000: break
        else:
            # KIS 국내 분봉 조회 (최근 수일치 수집 시도)
            current_date = datetime.now()
            days_to_fetch = 60 # 60영업일 수집
            offset_days = 60   # [수정] 60일 이전(과거 60~120일) 데이터만 가져오도록 offset 설정
            DATA_DAYS = 60
            LIMIT_COUNT = 10
            fetched_count = 0
            
            for d in range(offset_days, offset_days + days_to_fetch * 2): # 주말을 고려해 여유있게 range 설정
                target_date = current_date - timedelta(days=d)
                if target_date.weekday() >= 5: continue # 주말 제외
                
                date_str = target_date.strftime("%Y%m%d")
                # 15:30:00(장마감) 기준으로 과거 데이터 조회
                res = api.get_domestic_minute_chart(symbol, "153000", date_str)
                if res.get("output2"):
                    day_candles = []
                    for c in reversed(res["output2"]):
                        # backtester가 기대하는 공통 포맷으로 변환
                        day_candles.append({
                            "time": f"{c['stck_bsop_date']} {c['stck_cntg_hour'][:2]}:{c['stck_cntg_hour'][2:4]}",
                            "open": float(c["stck_oprc"]),
                            "high": float(c["stck_hgpr"]),
                            "low": float(c["stck_lwpr"]),
                            "close": float(c["stck_prpr"]),
                            "volume": float(c["cntg_vol"])
                        })
                    all_candles = day_candles + all_candles # 과거 데이터가 앞에 오도록
                    fetched_count += 1
                if fetched_count >= DATA_DAYS: break # [수정] 설정된 일수만큼 수집
        
        if not all_candles:
            logger.warning(f"{symbol} 데이터를 가져오지 못했습니다.")
            continue
            
        logger.info(f"총 {len(all_candles)}개의 1분봉 데이터 수집 완료. 백테스트 시작...")
        tester = SMCBacktester(symbol, all_candles, ai_model=ai_model, initial_capital=10000000.0)
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
        # my_trading_bot/ai/ 폴더 내부에 데이터 저장
        csv_path = os.path.join(base_dir, "my_trading_bot", "ai", "trading_data_for_ai.csv")
        file_exists = os.path.isfile(csv_path)
        df_ai.to_csv(csv_path, mode='a', header=not file_exists, index=False)
        print(f"\n[AI Data Collection] {len(df_ai)}개의 매매 기록이 {csv_path}에 저장되었습니다.")

if __name__ == "__main__":
    asyncio.run(main())
