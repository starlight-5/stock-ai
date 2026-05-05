# -*- coding: utf-8 -*-
"""
V1 SMC 전략 메인 봇 파이프라인 루프

[봇 상태 전환 흐름]
IDLE → MONITORING → STANDBY → IN_POSITION → COOLDOWN → MONITORING → ...
모든 상태에서 누적 손실 -5% 초과 시 → SHUTDOWN (킬 스위치)
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
    DAILY_DRAWDOWN_LIMIT, TRADE_RISK_RATIO,
    KILL_SWITCH_CHECK_INTERVAL_SEC, STANDBY_RECALC_INTERVAL_SEC,
    POI_CANDLE_COUNT_15M, ENTRY_CANDLE_COUNT_5M,
    TP1_CLOSE_RATIO, BUY_DVSN, SELL_DVSN, ORDER_TYPE_MARKET,
)
from .poi_detector import detect_fvg, detect_ob, is_price_in_poi, calculate_atr
from .sl_tp_calculator import calc_qty, calc_tp1, calc_tp2, calc_sl_price
from ...utils.market_schedule import (
    wait_for_market_open, is_market_open, is_market_closing_soon, now_et
)

logger = logging.getLogger(__name__)


class V1SmcBot(BaseStrategy):
    """
    MTFA(15분봉 POI + 5분봉 진입) + 동적 리스크 관리 + 분할 청산 + 킬 스위치
    전체 파이프라인을 총괄하는 봇 클래스입니다.
    """

    def __init__(self, api: KISApiHandler, symbol: str, excd: str, hts_id: str,
                 acnt_no: str = "", acnt_prdt_cd: str = "01"):
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

        # 봇 현재 상태
        self._state   = BotState.IDLE

        # 일일 통계 (킬 스위치 감시용)
        self._daily   = DailyStats()

        # 현재 포지션 정보
        self._pos     = PositionInfo()

        # 15분봉 기반 POI 구역 목록
        self._poi_zones: List[Dict[str, Any]] = []

        # 15분봉 캔들 캐시 (TP2 계산용)
        self._candles_15m: List[Dict[str, Any]] = []
        
        # 5분봉 기반 POI 구역 및 캔들 캐시
        self._poi_zones_5m: List[Dict[str, Any]] = []
        self._candles_5m: List[Dict[str, Any]] = []

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
        logger.info(f"[{self.symbol}] 백그라운드 태스크 시작 완료")
        return [kill_task, standby_task, close_task]

    # ──────────────────────────────────────────────
    # [0단계] 킬 스위치 - 잔고 스냅샷 및 감시 루프
    # ──────────────────────────────────────────────

    async def _stage0_snapshot_balance(self) -> None:
        """장 시작 시 계좌 잔고를 스냅샷으로 저장합니다."""
        res = await asyncio.to_thread(
            self._api.inquire_overseas_present_balance,
            self.acnt_no, self.acnt_prdt_cd
        )
        balance = self._parse_balance(res)
        self._daily.starting_balance = balance
        self._daily.current_balance  = balance
        logger.info(f"[0단계] 시작 잔고 스냅샷: ${balance:,.2f}")

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
            logger.info(f"[킬 스위치 감시] 누적 손실율: {drawdown:.2%}")

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

    async def _stage1_setup_poi(self) -> None:
        """15분봉 캔들을 조회하고 ATR을 계산한 후, FVG/OB 구역을 탐지하여 POI로 설정합니다."""
        logger.info("[1단계] 15분봉 POI 설정 중...")

        res = await asyncio.to_thread(
            self._api.get_time_itemchartprice,
            self.excd, self.symbol,
            "15",                          # 15분봉
            "1",                           # 조정 주가 반영
            str(POI_CANDLE_COUNT_15M),
        )

        candles = self._extract_candles(res)
        self._candles_15m = candles

        # ATR 계산: 변동성을 반영한 동적 FVG 필터에 활용
        atr = calculate_atr(candles)
        if atr:
            logger.info(f"[1단계] ATR = {atr:.5f} (동적 필터 적용)")
        else:
            logger.warning("[1단계] ATR 계산 불가 (캔들 부족) → 고정 비율 fallback 사용")

        fvg_zones = detect_fvg(candles, atr=atr)
        ob_zones  = detect_ob(candles, fvg_zones)

        # 롱 전략이므로 Bullish(지지) 구역만 POI로 사용
        self._poi_zones = [z for z in (fvg_zones + ob_zones) if z["type"] == "bullish"]

        logger.info(f"[1단계] POI {len(self._poi_zones)}개 설정 완료")


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
            # [2단계] 15분봉 POI 터치 여부 감시
            touched_zone = is_price_in_poi(price, self._poi_zones)
            if touched_zone:
                logger.info(f"[2단계] 15분봉 POI 터치! → STANDBY 전환 (price={price:.4f})")
                self._state = BotState.STANDBY
                # 즉시 5분봉 POI 탐지 실행
                await self._update_5m_poi()
                
        elif self._state == BotState.STANDBY:
            # [2.5단계] 5분봉 POI 터치 여부 감시
            if not self._is_ordering and getattr(self, '_poi_zones_5m', []):
                touched_zone = is_price_in_poi(price, self._poi_zones_5m)
                if touched_zone:
                    logger.info(f"[진입 신호] 5분봉 POI 터치! (price={price:.4f}) → 시장가 진입 실행")
                    await self._execute_entry(price)

        elif self._state == BotState.IN_POSITION:
            # [5단계] TP/SL 감시 및 분할 청산
            await self._stage5_manage_position(price)

    # ──────────────────────────────────────────────
    # [2.5단계] Standby 5분봉 POI 재계산 및 진입 실행
    # ──────────────────────────────────────────────
    
    async def _update_5m_poi(self) -> None:
        """5분봉 데이터를 조회하여 하위 POI(OB, FVG)를 최신화합니다."""
        logger.info("[2.5단계] 5분봉 POI 재분석 시작...")
        await asyncio.sleep(0.5) # API Rate limit 보호
        res = await asyncio.to_thread(
            self._api.get_time_itemchartprice,
            self.excd, self.symbol,
            "05", "1", str(ENTRY_CANDLE_COUNT_5M),
        )
        candles_5m = self._extract_candles(res)
        if not candles_5m:
            return
            
        self._candles_5m = candles_5m
        
        atr = calculate_atr(candles_5m)
        fvg = detect_fvg(candles_5m, atr=atr)
        ob = detect_ob(candles_5m, fvg)
        
        # 롱 전략이므로 Bullish(지지) 구역만 탐지
        self._poi_zones_5m = [z for z in (fvg + ob) if z["type"] == "bullish"]
        logger.info(f"[2.5단계] 5분봉 POI {len(self._poi_zones_5m)}개 갱신 완료")

    async def _standby_recalc_loop(self) -> None:
        """
        STANDBY 상태에서 5분마다 최신 5분봉 POI를 갱신합니다.
        실제 진입 감시는 웹소켓 실시간 가격 콜백에서 이루어집니다.
        """
        while self._running:
            await asyncio.sleep(STANDBY_RECALC_INTERVAL_SEC)

            if self._state == BotState.STANDBY:
                await self._update_5m_poi()

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
            
            if not self._candles_5m:
                logger.warning("[2.5단계] 5분봉 캔들 데이터 없음, 진입 취소")
                self._is_ordering = False
                return

            sl_price = calc_sl_price(self._candles_5m, direction="long")
            if not sl_price or sl_price >= entry_price:
                logger.warning(f"[2.5단계] 유효하지 않은 SL (SL={sl_price:.4f} >= 진입가={entry_price:.4f}), 진입 취소")
                self._is_ordering = False
                return

            qty = calc_qty(capital, TRADE_RISK_RATIO, entry_price, sl_price)
            if qty <= 0:
                logger.warning("[2.5단계] 리스크 한도 초과 또는 잔고 부족으로 진입 수량 0주")
                self._is_ordering = False
                return

            fvg_15m = detect_fvg(self._candles_15m)
            bearish_fvgs = [z for z in fvg_15m if z["type"] == "bearish"]

            tp1 = calc_tp1(entry_price, sl_price)
            tp2 = calc_tp2(entry_price, sl_price, self._candles_15m, bearish_fvgs)

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

        # ── TP2 터치 (전량 청산) ──
        elif pos.tp1_hit and price >= pos.tp2_price:
            logger.info(f"[5단계] TP2 달성! (price={price:.4f} >= tp2={pos.tp2_price:.4f})")
            await asyncio.to_thread(self._market_sell, pos.remaining_qty, "TP2 최종 청산")
            await self._end_cycle()

        # ── SL 터치 ──
        elif price <= pos.sl_price:
            if pos.tp1_hit:
                logger.info(f"[5단계] 본절 SL 터치 → 수익 마감 청산")
            else:
                logger.info(f"[5단계] SL 터치 → 손절 전량 청산")
            await asyncio.to_thread(self._market_sell, pos.remaining_qty, "SL 청산")
            await self._end_cycle()

    async def _end_cycle(self) -> None:
        """
        청산 완료 후 누적 손실을 재확인하고 다음 사이클을 준비합니다.
        """
        self._state = BotState.COOLDOWN
        logger.info("[사이클 종료] 잔고 및 누적 손실 재확인 중...")

        res = await asyncio.to_thread(
            self._api.inquire_overseas_present_balance,
            self.acnt_no, self.acnt_prdt_cd
        )
        self._daily.current_balance = self._parse_balance(res)
        self._daily.trade_count    += 1

        drawdown = self._daily.drawdown_ratio
        logger.info(f"[사이클 종료] 누적 손실율: {drawdown:.2%}")

        if drawdown <= DAILY_DRAWDOWN_LIMIT:
            logger.critical("사이클 종료 후 킬 스위치 조건 확인! 봇 종료.")
            await self.shutdown()
        else:
            # 포지션 정보 초기화 후 감시 재개
            self._pos   = PositionInfo()
            self._is_ordering = False
            self._poi_zones_5m = []
            self._state = BotState.MONITORING
            logger.info("[사이클 종료] 다음 매매 사이클 대기 시작 → MONITORING")

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
        """계좌 잔고 조회 응답에서 총 자산(USD)을 추출합니다."""
        try:
            output = res.get("output2", [{}])
            if isinstance(output, list) and output:
                return float(output[0].get("tot_asst_amt", 0))
            return float(res.get("output", {}).get("tot_asst_amt", 0))
        except (ValueError, TypeError):
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