# -*- coding: utf-8 -*-
"""
V1 SMC 전략 메인 봇 파이프라인 루프

이 모듈은 Smart Money Concept(SMC) 이론을 바탕으로 한 자동매매 봇의 핵심 로직을 담고 있습니다.
초보자분들을 위해 각 단계별 동작 원리를 상세히 설명합니다:

1. [상태 관리] 봇은 다음과 같은 흐름으로 상태를 변경하며 작동합니다:
   - IDLE: 초기 상태 또는 장 마감 후 대기
   - MONITORING: 5분봉 기준의 주요 지지/저항 구역(POI)에 가격이 들어오는지 감시
   - STANDBY: 5분봉 POI 터치 시, 1분봉 하위 POI를 분석하며 정밀 진입 타이밍 대기
   - IN_POSITION: 매수 체결 후 익절(TP) 또는 손절(SL) 도달을 실시간 감시
   - COOLDOWN: 매매 종료 후 다음 기회를 위해 잠시 대기
   - SHUTDOWN: 누적 손실이 과도할 경우 시스템 보호를 위해 즉시 중단 (킬 스위치)

2. [잔고 안정화] 한국투자증권 API의 응답 구조가 다양함에 따라 다음과 같은 안전 장치를 갖추고 있습니다:
   - 여러 응답 필드(output1~3)를 모두 탐색하여 자산 데이터를 정확히 추출합니다.
   - 1차 조회 실패 시 자동으로 보조 API를 호출하는 Fallback 메커니즘이 작동합니다.

3. [주요 구성 요소]
   - logic.py: 전체적인 매매 흐름(파이프라인)을 제어합니다.
   - poi_detector.py: 캔들 패턴을 분석하여 '세력의 흔적'인 FVG와 OB 구역을 찾아냅니다.
   - sl_tp_calculator.py: 리스크 관리 원칙에 따라 투입 수량과 목표가를 계산합니다.
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd
try:
    import xgboost as xgb
except ImportError:
    xgb = None

from ...core.api_handler import KISApiHandler
from ..base import BaseStrategy
from .state import BotState, DailyStats, PositionInfo
from .params import (
    DAILY_DRAWDOWN_LIMIT, TRADE_RISK_RATIO, ATR_PERIOD,
    KILL_SWITCH_CHECK_INTERVAL_SEC, STANDBY_RECALC_INTERVAL_SEC,
    POI_CANDLE_COUNT, ENTRY_CANDLE_COUNT,
    TP1_CLOSE_RATIO, BUY_DVSN, SELL_DVSN, ORDER_TYPE_MARKET,
    AI_PROB_THRESHOLD,
)
from .poi_detector import (
    detect_fvg, detect_ob, is_price_in_poi, calculate_atr, is_overlapping
)
from .sl_tp_calculator import calc_qty, calc_tp1, calc_tp2, calc_sl_price
from ...utils.market_schedule import (
    wait_for_market_open, is_market_open, is_market_closing_soon, now_et
)

logger = logging.getLogger(__name__)


class V1SmcBot(BaseStrategy):
    """
    MTFA(5분봉 POI + 1분봉 진입) + 동적 리스크 관리 + 분할 청산 + 킬 스위치
    전체 파이프라인을 총괄하는 봇 클래스입니다.
    """

    def __init__(self, api: KISApiHandler, symbol: str, excd: str, hts_id: str,
                 acnt_no: str = "", acnt_prdt_cd: str = "01", alpaca: Any = None,
                 ai_model: Any = None):
        """
        :param api:          초기화된 KISApiHandler 인스턴스
        :param symbol:       거래 종목 코드 (예: "AAPL")
        :param excd:         거래소 코드 (예: "NAS")
        :param hts_id:       체결통보 수신용 HTS ID
        :param acnt_no:      계좌번호 (알 8자리, .env 의 KIS_ACCOUNT_NO)
        :param acnt_prdt_cd: 계좌상품코드 (.env 의 KIS_ACCOUNT_PRODUCT_CODE)
        """
        self._api     = api
        self.symbol   = symbol
        self.excd     = excd
        self.hts_id   = hts_id
        self.acnt_no       = acnt_no
        self.acnt_prdt_cd  = acnt_prdt_cd
        self._alpaca       = alpaca
        self._ai_model     = ai_model
        
        # 콜백 함수들
        self.on_state_change = None
        self.on_trade = None

        # 봇 현재 상태
        self._state   = BotState.IDLE

        # 일일 통계 (킬 스위치 감시용)
        self._daily   = DailyStats()

        # 현재 포지션 정보
        self._pos     = PositionInfo()

        # POI 구역 목록 및 현재 활성화된(터치된) POI
        self._poi_zones: List[Dict[str, Any]] = []
        self._active_poi: Optional[Dict[str, Any]] = None

        # POI 분석용 캔들 캐시 (TP2 계산용)
        self._candles_poi: List[Dict[str, Any]] = []
        
        # 진입 분석용 POI 구역 및 캔들 캐시
        self._poi_zones_entry: List[Dict[str, Any]] = []
        self._candles_entry: List[Dict[str, Any]] = []

        # 내부 제어 플래그
        self._running = False
        self._is_ordering = False

    # ──────────────────────────────────────────────
    # [BaseStrategy 인터페이스 구현]
    # ──────────────────────────────────────────────

    def get_state(self) -> str:
        return self._state.value

    async def shutdown(self) -> None:
        """봇을 안전하게 종료합니다. 포지션이 있으면 전량 시장가 청산합니다."""
        logger.warning("=== 봇 강제 종료 시작 ===")
        self._running = False
        if self._state == BotState.IN_POSITION and self._pos.remaining_qty > 0:
            await asyncio.to_thread(self._market_sell_all, "강제 종료")
        self._state = BotState.SHUTDOWN
        logger.warning("=== 봇 종료 완료 ===")

    async def start_tasks(self) -> List[asyncio.Task]:
        """봇의 백그라운드 태스크(킬 스위치, 정정계산 등)를 시작하고 리스트를 반환합니다."""
        kill_task    = asyncio.create_task(self._kill_switch_loop())
        standby_task = asyncio.create_task(self._standby_recalc_loop())
        close_task   = asyncio.create_task(self._market_close_watcher())
        poi_task     = asyncio.create_task(self._poi_update_loop())
        
        self._state = BotState.MONITORING
        if self.on_state_change:
            await self.on_state_change(self.symbol, self._state.value)
        logger.info(f"[{self.symbol}] 백그라운드 태스크 시작 완료")
        return [kill_task, standby_task, close_task, poi_task]

    async def run(self) -> None:
        """
        BaseStrategy 인터페이스 구현: 봇의 메인 라이프사이클 관리
        
        본 봇은 웹소켓 기반으로 동작하며, 메인 로직은 start_tasks()에서 실행되는 
        백그라운드 루프와 웹소켓 콜백(process_ws_data)에서 처리됩니다.
        """
        try:
            if not self._running:
                await self.setup()
            
            # 백그라운드 태스크 시작
            tasks = await self.start_tasks()
            
            # 봇이 실행 중인 동안 대기
            while self._running:
                await asyncio.sleep(1)
                
            # 종료 시 태스크 취소
            for t in tasks:
                t.cancel()
                
        except asyncio.CancelledError:
            await self.shutdown()
        except Exception as e:
            logger.error(f"[{self.symbol}] 봇 실행 중 예외 발생: {e}", exc_info=True)
            await self.shutdown()

    # ──────────────────────────────────────────────
    # [0단계] 킬 스위치 - 잔고 스냅샷 및 감시 루프
    # ──────────────────────────────────────────────

    async def _stage0_snapshot_balance(self) -> None:
        """장 시작 시 계좌 잔고를 스냅샷으로 저장합니다."""
        logger.info(f"[0단계] 잔고 스냅샷 조회 시작... (계좌: {self.acnt_no}, 상품코드: {self.acnt_prdt_cd})")
        
        # 1차 시도: 체결기준 현재 잔고 (CTRP6504R)
        res = await asyncio.to_thread(
            self._api.inquire_overseas_present_balance,
            self.acnt_no, self.acnt_prdt_cd
        )
        balance = self._parse_balance(res)
        
        # 2차 시도 (Fallback): 만약 0원으로 나오면 일반 해외주식 잔고 API (TTTS3012R) 호출
        if balance <= 0:
            logger.warning("[0단계] 1차 잔고 조회 결과가 0입니다. Fallback API(TTTS3012R)를 호출합니다.")
            res_fb = await asyncio.to_thread(
                self._api.inquire_overseas_balance,
                self.acnt_no, self.acnt_prdt_cd, "NASD" # 기본적으로 나스닥 기준 조회
            )
            balance = self._parse_balance(res_fb)

        self._daily.starting_balance = balance
        self._daily.current_balance  = balance
        logger.info(f"[0단계] 최종 시작 잔고 스냅샷: ${balance:,.2f}")

    async def _poi_update_loop(self) -> None:
        """[주기적 갱신] 5분마다 최신 차트를 조회하여 POI를 재설정합니다."""
        while self._running:
            try:
                await asyncio.sleep(300) # 5분 대기
                
                # MONITORING 상태일 때만 갱신 (이미 진입 중이거나 대기 중일 때는 건너뜀)
                if self._state == BotState.MONITORING:
                    logger.info(f"[{self.symbol}] 5분 주기 POI 최신화 시작...")
                    await self._stage1_setup_poi()
                else:
                    logger.debug(f"[{self.symbol}] 현재 상태({self._state.value})가 MONITORING이 아니므로 POI 갱신을 건너뜁니다.")
            
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self.symbol}] POI 갱신 루프 중 오류: {e}")
                await asyncio.sleep(60) # 에러 시 1분 후 재시도

    async def _kill_switch_loop(self) -> None:
        """
        주기적으로 잔고를 조회하고, 누적 손실이 한도를 초과하면 킬 스위치를 발동합니다.
        """
        while self._running:
            await asyncio.sleep(KILL_SWITCH_CHECK_INTERVAL_SEC)

            if self._state == BotState.SHUTDOWN:
                break

            res = await asyncio.to_thread(
                self._api.inquire_overseas_present_balance,
                self.acnt_no, self.acnt_prdt_cd
            )
            self._daily.current_balance = self._parse_balance(res)

            drawdown = self._daily.drawdown_ratio
            logger.info(f"[{self.symbol}] [킬 스위치 감시] 누적 손실율: {drawdown:.2%}")

            if drawdown <= DAILY_DRAWDOWN_LIMIT:
                logger.critical(f"누적 손실 한도 초과({drawdown:.2%})! 킬 스위치 발동!")
                await self.shutdown()
                break

    # ──────────────────────────────────────────────
    # [장 마감 감시] 장 종료 시 웹소켓 연결 해제 트리거
    # ──────────────────────────────────────────────

    async def _market_close_watcher(self) -> None:
        """
        미국 정규장이 마감(16:00 ET)되면 신규 진입을 차단하고,
        웹소켓 연결을 안전하게 종료하도록 상태를 변경합니다.
        포지션이 남아 있으면 전량 시장가로 청산합니다.
        """
        while self._running:
            await asyncio.sleep(30)  # 30초마다 체크

            if not is_market_open():
                logger.info("[장 마감] 정규장이 종료되었습니다.")

                # 포지션이 있으면 전량 청산
                if self._state == BotState.IN_POSITION and self._pos.remaining_qty > 0:
                    logger.warning("[장 마감] 잔여 포지션을 전량 시장가 청산합니다.")
                    await asyncio.to_thread(self._market_sell_all, "장 마감 강제 청산")

                # 상태를 IDLE로 변경하여 웹소켓 루프를 자연스럽게 종료
                self._state = BotState.IDLE
                self._running = False  # run() 루프가 장 대기로 넘어가도록
                break

            # 장 마감 30분 전: 신규 진입 차단 (MONITORING → IDLE)
            if is_market_closing_soon(minutes=30) and self._state == BotState.MONITORING:
                logger.info("[장 마감 임박] 30분 전 — 신규 진입을 차단합니다.")
                self._state = BotState.IDLE

    async def _stage1_setup_poi(self) -> None:
        """[1단계] 5분봉 데이터를 조회하여 초기 POI(OB/FVG)를 설정합니다."""
        logger.info(f"[{self.symbol}] 1단계: 초기 POI 탐지 시작 (거래소: {self.excd}, 개수: {POI_CANDLE_COUNT})")
        
        # 5분봉 조회 (KIS)
        res = await asyncio.to_thread(
            self._api.get_time_itemchartprice,
            self.excd, self.symbol,
            "05",                          # 5분봉 (기존 15분봉에서 변경)
            "1",                           # 조정 주가 반영
            str(POI_CANDLE_COUNT),
        )

        candles = self._extract_candles(res)
        if not candles or len(candles) == 0:
            logger.warning(f"[{self.symbol}] KIS 데이터 반환 결과가 0개입니다. (응답 메시지: {res.get('msg1')})")
            self._candles_poi = []
        else:
            self._candles_poi = candles

        # Alpaca를 통한 데이터 보완 (KIS 데이터 부족 시)
        if (not candles or len(candles) < ATR_PERIOD + 1) and self._alpaca:
            logger.info(f"[1단계] KIS 데이터 부족({len(candles)}개), Alpaca에서 보완 시도...")
            # AlpacaClient는 이미 formatted_candles를 반환하도록 구현함
            alpaca_candles = await self._alpaca.get_historical_candles(
                self.symbol, timeframe="5Min", limit=POI_CANDLE_COUNT
            )
            if alpaca_candles:
                logger.info(f"[1단계] Alpaca에서 {len(alpaca_candles)}개의 데이터를 가져왔습니다.")
                self._candles_poi = alpaca_candles
                candles = self._candles_poi

        # ATR 계산: 변동성을 반영한 동적 FVG 필터에 활용
        atr = calculate_atr(candles)
        if atr:
            logger.info(f"[1단계] ATR = {atr:.5f} (동적 필터 적용)")
        else:
            logger.warning(f"[1단계] ATR 계산 불가 (캔들 {len(candles)}개) → 고정 비율 fallback 사용")

        fvg_zones = detect_fvg(candles, atr=atr)
        ob_zones  = detect_ob(candles, fvg_zones)

        # 롱 전략이므로 Bullish(지지) 구역만 POI로 사용
        self._poi_zones = [z for z in (fvg_zones + ob_zones) if z["type"] == "bullish"]

        logger.info(f"[1단계] POI {len(self._poi_zones)}개 설정 완료")
        for i, zone in enumerate(self._poi_zones):
            logger.info(f"  - POI #{i+1}: [{zone['type_label']}] 범위: {zone['low']:.4f} ~ {zone['high']:.4f}")
    # ──────────────────────────────────────────────
    # [2단계] 웹소켓 콜백 - 실시간 POI 터치 감시
    # ──────────────────────────────────────────────

    async def setup(self) -> None:
        """봇 실행을 위한 초기 설정을 수행합니다."""
        self._running = True
        self._daily = DailyStats()
        await self._stage0_snapshot_balance()
        await asyncio.sleep(0.5) # API Rate limit 보호
        await self._stage1_setup_poi()

    async def process_ws_data(self, tr_id: str, tr_data: str) -> None:
        """
        중앙 매니저로부터 웹소켓 데이터를 전달받아 처리합니다.
        
        :param tr_id: 트랜잭션 ID (HDFSCNT0, H0GSCNI0 등)
        :param tr_data: 실시간 데이터 본문
        """
        if self._state == BotState.SHUTDOWN:
            return

        # 실시간 지연 체결가 수신 (HDFSCNT0)
        if tr_id == "HDFSCNT0":
            price = self._parse_realtime_price(tr_data)
            if price:
                await self._handle_price_update(price)

        # 체결통보 수신 (H0GSCNI0)
        elif tr_id == "H0GSCNI0":
            await self._stage4_confirm_fill(tr_data)

    async def _handle_price_update(self, price: float) -> None:
        """실시간 가격 수신 시 현재 상태에 따라 처리합니다."""
        if self._state == BotState.SHUTDOWN:
            return

        if self._state == BotState.MONITORING:
            # [2단계] 5분봉 POI 터치 여부 감시
            touched_zone = is_price_in_poi(price, self._poi_zones)
            if touched_zone:
                logger.info(
                    f"[2단계] 5분봉 POI 터치! ({touched_zone['type_label']} | "
                    f"범위: {touched_zone['low']:.4f}~{touched_zone['high']:.4f}) "
                    f"→ STANDBY 전환 (현재가={price:.4f})"
                )
                self._state = BotState.STANDBY
                self._active_poi = touched_zone
                if self.on_state_change:
                    await self.on_state_change(self.symbol, self._state.value)
                # 즉시 1분봉 POI 탐지 실행
                await self._update_entry_poi()
                
        elif self._state == BotState.STANDBY:
            # [상태 관리] 가격이 POI를 완전히 벗어나면 다시 MONITORING으로 복구
            if self._active_poi:
                exit_threshold = self._active_poi["high"] * 1.01 
                if price > exit_threshold:
                    logger.info(f"[상태 복구] 가격({price:.4f})이 5분봉 POI 범위를 벗어남 -> MONITORING 복귀")
                    self._state = BotState.MONITORING
                    self._active_poi = None
                    self._poi_zones_entry = []
                    if self.on_state_change:
                        await self.on_state_change(self.symbol, self._state.value)
                    return

            # [2.5단계] 1분봉 POI 터치 여부 감시
            if not self._is_ordering and getattr(self, '_poi_zones_entry', []):
                touched_zone = is_price_in_poi(price, self._poi_zones_entry)
                if touched_zone:
                    logger.info(f"[진입 신호] 1분봉 POI 터치! (price={price:.4f}) → 시장가 진입 실행")
                    await self._execute_entry(price)

        elif self._state == BotState.IN_POSITION:
            # [5단계] TP/SL 감시 및 분할 청산
            await self._stage5_manage_position(price)

    # ──────────────────────────────────────────────
    # [2.5단계] Standby 5분봉 POI 재계산 및 진입 실행
    # ──────────────────────────────────────────────
    
    async def _update_entry_poi(self) -> None:
        """1분봉 데이터를 조회하여 하위 POI(OB, FVG)를 최신화합니다."""
        logger.info("[2.5단계] 1분봉 POI 재분석 시작...")
        await asyncio.sleep(0.5) # API Rate limit 보호
        res = await asyncio.to_thread(
            self._api.get_time_itemchartprice,
            self.excd, self.symbol,
            "01", "1", str(ENTRY_CANDLE_COUNT),
        )
        candles_entry = self._extract_candles(res)
        
        # Alpaca를 통한 데이터 보완 (1분봉)
        if len(candles_entry) < ATR_PERIOD + 1 and self._alpaca:
            logger.info(f"[2.5단계] KIS 1분봉 부족({len(candles_entry)}개), Alpaca 보완 시도...")
            alpaca_entry = await self._alpaca.get_historical_candles(
                self.symbol, timeframe="1Min", limit=ENTRY_CANDLE_COUNT
            )
            if alpaca_entry:
                candles_entry = alpaca_entry

        if not candles_entry:
            return
            
        self._candles_entry = candles_entry
        
        atr = calculate_atr(candles_entry)
        fvg = detect_fvg(candles_entry, atr=atr)
        ob = detect_ob(candles_entry, fvg)
        
        # 롱 전략이므로 Bullish(지지) 구역만 탐지
        all_bullish_entry = [z for z in (fvg + ob) if z["type"] == "bullish"]
        
        # [중첩 필터링 완화] 상위 POI 터치 후에는 1분봉에서 발생하는 모든 지지 POI를 인정합니다.
        # 기존에는 상위 POI와 물리적으로 겹쳐야만 했으나, 실전 변동성을 고려하여 범위를 넓혔습니다.
        self._poi_zones_entry = all_bullish_entry
        logger.info(f"[2.5단계] 1분봉 POI {len(self._poi_zones_entry)}개 갱신 완료 (조건 완화 적용)")

        for zone in self._poi_zones_entry:
            logger.info(f"  - Entry POI: [{zone['type_label']}] {zone['low']:.4f} ~ {zone['high']:.4f}")

    async def _standby_recalc_loop(self) -> None:
        """
        STANDBY 상태에서 주기적으로 최신 진입 POI를 갱신합니다.
        """
        while self._running:
            await asyncio.sleep(STANDBY_RECALC_INTERVAL_SEC)

            if self._state == BotState.STANDBY:
                await self._update_entry_poi()

    async def _execute_entry(self, entry_price: float) -> None:
        """5분봉 POI 터치 시 SL, TP, 수량을 즉시 계산하고 진입합니다."""
        if self._state != BotState.STANDBY or getattr(self, '_is_ordering', False):
            return
            
        self._is_ordering = True
        try:
            # AI 필터링 (XGBoost)
            if self._ai_model:
                features = self._calculate_indicators(self._candles_entry)
                if features:
                    # 백테스트와 동일한 순서로 피처 구성
                    feature_cols = ["entry_hour", "atr_5m", "rsi_5m", "disparity_5m", "fvg_size_ratio", "volume_ma_ratio"]
                    input_df = pd.DataFrame([[features[c] for c in feature_cols]], columns=feature_cols)
                    
                    # XGBoost 예측 (확률 추출)
                    prob = self._ai_model.predict_proba(input_df)[0][1]
                    
                    if prob < AI_PROB_THRESHOLD:
                        logger.warning(
                            f"[{self.symbol}] AI 필터 진입 거절: 승률 예측 {prob:.2%} < 기준 {AI_PROB_THRESHOLD:.2%}"
                        )
                        self._is_ordering = False
                        self._state = BotState.MONITORING
                        return
                    else:
                        logger.info(f"[{self.symbol}] AI 필터 진입 승인: 승률 예측 {prob:.2%}")

            # 잔고 조회
            bal_res = await asyncio.to_thread(
                self._api.inquire_overseas_present_balance,
                self.acnt_no, self.acnt_prdt_cd
            )
            capital = self._parse_balance(bal_res)
            
            sl_price = calc_sl_price(self._candles_entry, direction="long")
            if not sl_price or sl_price >= entry_price:
                logger.warning(f"[2.5단계] 유효하지 않은 SL (SL={sl_price:.4f} >= 진입가={entry_price:.4f}), 진입 취소")
                self._is_ordering = False
                return

            qty = calc_qty(capital, TRADE_RISK_RATIO, entry_price, sl_price)
            if qty <= 0:
                logger.warning("[2.5단계] 리스크 한도 초과 또는 잔고 부족으로 진입 수량 0주")
                self._is_ordering = False
                return
            
            # [최소 리스크 거리 필터 추가] 
            # 수수료 0.5%를 압도하기 위해 리스크 거리(R)가 최소 0.8% 이상인 경우에만 진입
            risk_pct = (entry_price - sl_price) / entry_price
            if risk_pct < 0.008:
                logger.warning(f"[{self.symbol}] 최소 리스크 거리 미달로 진입 취소 (R={risk_pct:.2%})")
                self._is_ordering = False
                return

            fvg_poi = detect_fvg(self._candles_poi)
            bearish_fvgs = [z for z in fvg_poi if z["type"] == "bearish"]

            tp1 = calc_tp1(entry_price, sl_price)
            tp2 = calc_tp2(entry_price, sl_price, self._candles_poi, bearish_fvgs)

            # 포지션 정보 기록
            self._pos.symbol = self.symbol
            self._pos.excd = self.excd
            self._pos.entry_price = entry_price
            self._pos.total_qty = qty
            self._pos.remaining_qty = qty
            self._pos.sl_price = sl_price
            self._pos.initial_sl_price = sl_price # 트레일링 스탑용 초기 손절가 저장
            self._pos.tp1_price = tp1
            self._pos.tp2_price = tp2

            logger.info(
                f"[진입 계산 완료] "
                f"진입가={entry_price:.4f}, SL={sl_price:.4f}, "
                f"TP1={tp1:.4f}, TP2={tp2:.4f}, 수량={qty}주"
            )

            await self._stage3_enter()
            
        except Exception as e:
            logger.error(f"[진입 에러] 5분봉 POI 터치 진입 중 에러 발생: {e}")
            self._is_ordering = False

    # ──────────────────────────────────────────────
    # [3단계] 확정 진입 주문 실행
    # ──────────────────────────────────────────────

    async def _stage3_enter(self) -> None:
        """2.5단계에서 계산된 값으로 매수 주문을 실행합니다."""
        if self._state != BotState.STANDBY:
            return

        logger.info(
            f"[3단계] 매수 주문 실행 → {self.symbol} {self._pos.total_qty}주 "
            f"@ {self._pos.entry_price:.4f}"
        )

        res = await asyncio.to_thread(
            self._api.order_overseas_stock,
            ord_dvsn=BUY_DVSN,
            ovrs_excg_cd=self.excd,
            pdno=self.symbol,
            ft_ord_qty=str(self._pos.total_qty),
            ft_ord_unpr3=f"{self._pos.entry_price:.4f}",
        )

        order_no = res.get("output", {}).get("odno", "")
        self._pos.order_no = order_no
        logger.info(f"[3단계] 주문 접수 완료: 주문번호={order_no}")
        if self.on_trade:
            await self.on_trade(self.symbol, "BUY_ORDER", {"price": self._pos.entry_price, "qty": self._pos.total_qty, "order_no": order_no})

    # ──────────────────────────────────────────────
    # [4단계] 체결 확인 (웹소켓 체결통보)
    # ──────────────────────────────────────────────

    async def _stage4_confirm_fill(self, tr_data: str) -> None:
        """
        체결통보(H0GSCNI0) 수신 시 내 주문의 체결 여부를 확인하고
        상태를 IN_POSITION으로 전환합니다.
        """
        if self._state != BotState.STANDBY:
            return

        # tr_data는 '^' 구분자로 필드가 구성됨 (KIS 규격)
        fields = tr_data.split("^")
        if len(fields) < 5:
            return

        # 체결 여부: 필드[2] == "2" → 체결
        filled = fields[2] == "2" if len(fields) > 2 else False

        if filled:
            logger.info("[4단계] 체결 확인! → IN_POSITION 전환")
            self._state = BotState.IN_POSITION
            if self.on_state_change:
                await self.on_state_change(self.symbol, self._state.value)

    # ──────────────────────────────────────────────
    # [5단계] 포지션 관리 및 분할 청산
    # ──────────────────────────────────────────────

    async def _stage5_manage_position(self, price: float) -> None:
        """
        실시간 가격이 TP1/TP2/SL에 도달하는지 감시하고,
        도달 시 분할 청산 또는 전량 청산을 실행합니다.
        """
        pos = self._pos

        # ── [트레일링 수익 확보] ──
        # 수익이 리스크의 2배(2.0R) 도달 시, 손절가를 1.0R 수익 지점으로 상향하여 수익 확정
        r_dist = pos.entry_price - pos.initial_sl_price
        if not pos.tp1_hit and price >= (pos.entry_price + r_dist * 2.0):
            pos.sl_price = pos.entry_price + r_dist * 1.0
            pos.tp1_hit = True # 트레일링 발동 플래그로 재사용
            logger.info(f"[5단계] 트레일링 스탑 발동: 1.0R 수익 확보 (SL={pos.sl_price:.4f})")
            if self.on_trade:
                await self.on_trade(self.symbol, "TRAILING_STOP", {"price": price, "new_sl": pos.sl_price})
            return

        # ── TP2 터치 (전량 청산) ──
        # 트레일링 스탑(tp1_hit)이 발동된 상태에서 TP2 목표가 도달 시 전량 수익 실현
        if pos.tp1_hit and price >= pos.tp2_price:
            logger.info(f"[5단계] TP2 달성! (price={price:.4f} >= tp2={pos.tp2_price:.4f})")
            await asyncio.to_thread(self._market_sell, pos.remaining_qty, "TP2 최종 청산")
            if self.on_trade:
                await self.on_trade(self.symbol, "TP2_SELL", {"price": price, "qty": pos.remaining_qty})
            await self._end_cycle()
            return

        # ── SL 터치 ──
        elif price <= pos.sl_price:
            if pos.tp1_hit:
                logger.info(f"[5단계] 본절 SL 터치 → 수익 마감 청산")
            else:
                logger.info(f"[5단계] SL 터치 → 손절 전량 청산")
            await asyncio.to_thread(self._market_sell, pos.remaining_qty, "SL 청산")
            if self.on_trade:
                await self.on_trade(self.symbol, "SL_SELL", {"price": price, "qty": pos.remaining_qty})
            await self._end_cycle()

    async def _end_cycle(self) -> None:
        """
        청산 완료 후 누적 손실을 재확인하고 다음 사이클을 준비합니다.
        """
        self._state = BotState.COOLDOWN
        logger.info(f"[{self.symbol}] [사이클 종료] 잔고 및 누적 손실 재확인 중...")

        res = await asyncio.to_thread(
            self._api.inquire_overseas_present_balance,
            self.acnt_no, self.acnt_prdt_cd
        )
        self._daily.current_balance = self._parse_balance(res)
        self._daily.trade_count    += 1

        drawdown = self._daily.drawdown_ratio
        logger.info(f"[{self.symbol}] [사이클 종료] 누적 손실율: {drawdown:.2%}")

        if drawdown <= DAILY_DRAWDOWN_LIMIT:
            logger.critical(f"[{self.symbol}] 사이클 종료 후 킬 스위치 조건 확인! 봇 종료.")
            await self.shutdown()
        else:
            # 포지션 정보 초기화 후 감시 재개
            self._pos   = PositionInfo()
            self._is_ordering = False
            self._poi_zones_entry = []
            self._state = BotState.MONITORING
            logger.info(f"[{self.symbol}] [사이클 종료] 다음 매매 사이클 대기 시작 → MONITORING")

    # ──────────────────────────────────────────────
    # [내부 헬퍼 메서드]
    # ──────────────────────────────────────────────

    def _market_sell(self, qty: int, reason: str = "") -> None:
        """동기 방식으로 시장가 매도 주문을 실행합니다. (asyncio.to_thread 용) 통신 오류 방지를 위해 최대 3회 재시도합니다."""
        for attempt in range(1, 4):
            try:
                logger.info(f"매도 주문 시도 ({attempt}/3): {self.symbol} {qty}주 ({reason})")
                res = self._api.order_overseas_stock(
                    ord_dvsn=SELL_DVSN,
                    ovrs_excg_cd=self.excd,
                    pdno=self.symbol,
                    ft_ord_qty=str(qty),
                    ft_ord_unpr3="0",  # 시장가
                )
                # KIS API 응답 코드가 "0"이면 성공
                if res.get("rt_cd") == "0":
                    logger.info(f"매도 주문 성공: {self.symbol} {qty}주")
                    return
                else:
                    logger.warning(f"매도 주문 응답 에러 ({attempt}/3): {res.get('msg1')}")
            except Exception as e:
                logger.error(f"매도 주문 통신 오류 ({attempt}/3): {e}")
            
            if attempt < 3:
                time.sleep(1) # 1초 대기 후 재시도
        
        logger.critical(f"매도 주문 최종 실패 (3회 시도 초과): {self.symbol} {qty}주 미청산 상태입니다!")

    def _market_sell_all(self, reason: str = "") -> None:
        """전량 시장가 매도합니다."""
        if self._pos.remaining_qty > 0:
            self._market_sell(self._pos.remaining_qty, reason)

    def _calculate_indicators(self, candles: List[Dict[str, Any]]) -> Optional[Dict[str, float]]:
        """AI 학습에 필요한 기술적 지표를 계산합니다. (백테스트 로직과 동기화)"""
        if len(candles) < 20: return None
        try:
            closes = np.array([float(c.get("close", c.get("last", 0))) for c in candles])
            highs = np.array([float(c.get("high", c.get("hipr", 0))) for c in candles])
            lows = np.array([float(c.get("low", c.get("lopr", 0))) for c in candles])
            volumes = np.array([float(c.get("volume", c.get("tvol", 0))) for c in candles])
            
            # 1. RSI (14) - Simple Moving Average 기반
            delta = np.diff(closes)
            gain = np.where(delta > 0, delta, 0)
            loss = np.where(delta < 0, -delta, 0)
            avg_gain = np.mean(gain[-14:])
            avg_loss = np.mean(loss[-14:])
            rsi = 100 - (100 / (1 + (avg_gain / (avg_loss + 1e-9))))
            
            # 2. ATR (14) - numpy 경고 해결
            tr1 = highs[1:] - lows[1:]
            tr2 = np.abs(highs[1:] - closes[:-1])
            tr3 = np.abs(lows[1:] - closes[:-1])
            tr = np.maximum(np.maximum(tr1, tr2), tr3)
            atr = np.mean(tr[-14:])
            
            # 3. 이격도 (20일 이평선 기준, 백테스트와 동일하게 1.0x 규격)
            sma20 = np.mean(closes[-20:])
            disparity = closes[-1] / (sma20 + 1e-9)
            
            # 4. FVG Size Ratio (현재가와 SL의 거리를 가격으로 나눈 비율)
            # 이 시점에는 SL이 확정되지 않았으므로 대략적인 ATR 비율로 대체하거나
            # 외부에서 계산된 값을 사용해야 함. 여기서는 백테스트와 동일한 공식을 위해
            # 호출 시점의 정보를 활용.
            current_price = closes[-1]
            # 임시 SL (직전 캔들 저가) 기준 비율 계산
            temp_sl = np.min(lows[-3:])
            fvg_size_ratio = abs(current_price - temp_sl) / (current_price + 1e-9)
            
            # 5. 거래량 변화율
            vol_ma_ratio = volumes[-1] / (np.mean(volumes[-20:]) + 1e-9)
            
            return {
                "entry_hour": datetime.now().hour,
                "atr_5m": atr,
                "rsi_5m": rsi,
                "disparity_5m": disparity,
                "fvg_size_ratio": fvg_size_ratio,
                "volume_ma_ratio": vol_ma_ratio
            }
        except Exception as e:
            logger.error(f"지표 계산 중 오류: {e}")
            return None

    def _extract_candles(self, res: Dict[str, Any]) -> List[Dict[str, Any]]:
        """KIS API 응답에서 캔들 리스트를 추출하고 필드명을 표준화합니다."""
        output = res.get("output2", res.get("output", []))
        if not isinstance(output, list):
            return []
            
        normalized = []
        for c in output:
            try:
                # KIS 필드명을 표준 이름으로 매핑 (문자열인 경우 float 변환)
                candle = {
                    "open": float(c.get("stck_oprc", c.get("open", 0))),
                    "high": float(c.get("stck_hgpr", c.get("high", 0))),
                    "low": float(c.get("stck_lwpr", c.get("low", 0))),
                    "close": float(c.get("stck_clpr", c.get("close", 0))),
                    "volume": float(c.get("tvol", c.get("volume", 0))),
                    "time": c.get("stck_cntg_hour", c.get("time", ""))
                }
                normalized.append(candle)
            except (ValueError, TypeError):
                continue
                
        # 시간순으로 정렬 (KIS는 보통 최신순으로 줌)
        return sorted(normalized, key=lambda x: x["time"])

    def _parse_balance(self, res: Dict[str, Any]) -> float:
        """
        계좌 잔고 조회 응답에서 자산(USD)을 추출합니다.
        지원 API: CTRP6504R (체결기준 현재잔고), TTTS3012R (해외주식 잔고)
        """
        if not res:
            logger.error("[Balance] API 응답이 비어있습니다.")
            return 0.0

        rt_cd = res.get("rt_cd")
        msg1 = res.get("msg1", "").strip()
        if rt_cd != "0":
            logger.warning(f"[Balance] KIS API 응답 에러: [{rt_cd}] {msg1}")
            logger.debug(f"[Balance] Error Response Raw: {res}")

        try:
            # 자산 금액 후보 필드 (우선순위: 총자산 -> 총외화잔고 -> 예수금)
            asset_keys = [
                "tot_asst_amt",          # 총자산금액
                "tot_frcr_cblc_smtl",    # 총외화잔액합계
                "frcr_evlu_amt2",        # 외화평가금액2
                "tot_evlu_amt",          # 총평가금액
                "tot_dncl_amt",          # 총예수금 (현금)
                "dncl_amt",              # 예수금
                "wdrw_psbl_tot_amt",     # 출금가능총액
                "ovrs_tot_evlu_amt",     # 해외총평가금액
                "tot_asst_amt_usd",      # 총자산금액(USD)
                "frcr_evlu_tota"         # 외화평가총액
            ]

            def _to_float(val: Any) -> float:
                if val is None: return 0.0
                if isinstance(val, (int, float)): return float(val)
                s_val = str(val).replace(",", "").strip()
                try:
                    return float(s_val) if s_val else 0.0
                except ValueError:
                    return 0.0

            # 1. output3 확인 (요약 정보가 위치하는 최우선 순위)
            output3 = res.get("output3")
            if isinstance(output3, dict):
                for key in asset_keys:
                    val = _to_float(output3.get(key))
                    if val > 0:
                        logger.debug(f"[Balance] Found value {val} in output3['{key}']")
                        return val

            # 2. output2 및 output/output1 확인
            for out_key in ["output2", "output", "output1"]:
                out_data = res.get(out_key)
                if not out_data:
                    continue
                
                # 리스트면 첫 번째 항목, 딕셔너리면 그대로 사용
                data = out_data[0] if isinstance(out_data, list) and out_data else out_data
                if isinstance(data, dict):
                    for key in asset_keys:
                        val = _to_float(data.get(key))
                        if val > 0:
                            logger.debug(f"[Balance] Found value {val} in {out_key}['{key}']")
                            return val

            # 모든 필드 확인 후에도 0이면
            if rt_cd == "0":
                logger.warning(f"[Balance] 모든 자산 필드가 0입니다. (정상적 0원 계좌이거나 필드 매칭 실패)")
                logger.debug(f"[Balance] Full Response for debugging: {res}")

            return 0.0

        except Exception as e:
            logger.error(f"[Balance] 파싱 중 예외 발생: {e} | 응답 요약: {str(res)[:200]}")
            return 0.0

    def _parse_current_price(self, res: Dict[str, Any]) -> Optional[float]:
        """현재가 조회 응답에서 체결가를 추출합니다."""
        try:
            return float(res.get("output", {}).get("last", 0))
        except (ValueError, TypeError):
            return None

    def _parse_realtime_price(self, tr_data: str) -> Optional[float]:
        """웹소켓 실시간 체결가 데이터에서 가격을 추출합니다."""
        try:
            fields = tr_data.split("^")
            # HDFSCNT0: 필드[11]이 체결가
            return float(fields[11]) if len(fields) > 11 else None
        except (ValueError, IndexError):
            return None