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

from ...core.api_handler import KISApiHandler
from ..base import BaseStrategy
from .state import BotState, DailyStats, PositionInfo
from .params import (
    DAILY_DRAWDOWN_LIMIT, TRADE_RISK_RATIO, ATR_PERIOD,
    KILL_SWITCH_CHECK_INTERVAL_SEC, STANDBY_RECALC_INTERVAL_SEC,
    POI_CANDLE_COUNT, ENTRY_CANDLE_COUNT,
    TP1_CLOSE_RATIO, BUY_DVSN, SELL_DVSN, ORDER_TYPE_MARKET,
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
                 acnt_no: str = "", acnt_prdt_cd: str = "01", alpaca: Any = None):
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
        
        self._state = BotState.MONITORING
        if self.on_state_change:
            await self.on_state_change(self.symbol, self._state.value)
        logger.info(f"[{self.symbol}] 백그라운드 태스크 시작 완료")
        return [kill_task, standby_task, close_task]

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

    # ──────────────────────────────────────────────
    # [1단계] 15분봉 POI 설정
    # ──────────────────────────────────────────────

        res = await asyncio.to_thread(
            self._api.get_time_itemchartprice,
            self.excd, self.symbol,
            "05",                          # 5분봉 (기존 15분봉에서 변경)
            "1",                           # 조정 주가 반영
            str(POI_CANDLE_COUNT),
        )

        candles = self._extract_candles(res)
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
        
        # [중첩 필터링] POI와 겹치는(Overlap) 진입 POI만 유효한 것으로 인정
        if self._active_poi:
            self._poi_zones_entry = [
                z for z in all_bullish_entry 
                if is_overlapping(self._active_poi, z)
            ]
            logger.info(
                f"[2.5단계] 중첩 필터링 완료: {len(all_bullish_entry)}개 중 "
                f"{len(self._poi_zones_entry)}개 유효 (상위 POI와 중첩)"
            )
        else:
            self._poi_zones_entry = all_bullish_entry
            logger.info(f"[2.5단계] 1분봉 POI {len(self._poi_zones_entry)}개 갱신 완료 (상위 POI 정보 없음)")

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

        # ── TP1 터치 (1차 부분 익절) ──
        if not pos.tp1_hit and price >= pos.tp1_price:
            logger.info(f"[5단계] TP1 달성! (price={price:.4f} >= tp1={pos.tp1_price:.4f})")
            half_qty = max(1, int(pos.remaining_qty * TP1_CLOSE_RATIO))
            await asyncio.to_thread(self._market_sell, half_qty, "TP1 부분 청산")

            pos.remaining_qty -= half_qty
            pos.tp1_hit        = True
            # 본절컷(Breakeven): 남은 수량의 SL을 진입가로 상향
            pos.sl_price       = pos.entry_price
            logger.info(f"[5단계] 본절 방어 설정: SL → 진입가 {pos.entry_price:.4f}")
            if self.on_trade:
                await self.on_trade(self.symbol, "TP1_SELL", {"price": price, "qty": half_qty})

        # ── TP2 터치 (전량 청산) ──
        elif pos.tp1_hit and price >= pos.tp2_price:
            logger.info(f"[5단계] TP2 달성! (price={price:.4f} >= tp2={pos.tp2_price:.4f})")
            await asyncio.to_thread(self._market_sell, pos.remaining_qty, "TP2 최종 청산")
            if self.on_trade:
                await self.on_trade(self.symbol, "TP2_SELL", {"price": price, "qty": pos.remaining_qty})
            await self._end_cycle()

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

    def _extract_candles(self, res: Dict[str, Any]) -> List[Dict[str, Any]]:
        """KIS API 응답에서 캔들 리스트를 추출합니다."""
        output = res.get("output2", res.get("output", []))
        if isinstance(output, list):
            return output
        return []

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