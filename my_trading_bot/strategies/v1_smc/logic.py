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
from .poi_detector import detect_fvg, detect_ob, is_price_in_poi
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

        # 내부 제어 플래그
        self._running = False

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

    async def run(self) -> None:
        """
        메인 비동기 루프를 시작합니다.
        1) 토큰 발급 및 일일 잔고 스냅샷
        2) 웹소켓 연결 및 감시 시작
        3) Kill Switch 감시 루프 (백그라운드 태스크)
        4) Standby 재계산 루프 (백그라운드 태스크)
        """
        self._running = True
        logger.info(f"봇 시작: {self.excd}/{self.symbol}")

        # ── 미국 정규장 대기 루프 외부 실행문 ──
        # 정규장이 열릴 때까지 대기하다가 매일 장 시작 시 자동 재실행됩니다.
        while self._running:
            # 정규장 오픈까지 대기
            await wait_for_market_open()

            if not self._running:
                break

            logger.info(f"[장 시작] ET {now_et().strftime('%H:%M:%S')} — 하루 매매 시작")

            # [인증] 액세스 토큰 + 웹소켓 접속키 발급
            self._api.issue_access_token()
            self._api.connect_ws()

            # [0단계] 시작 잔고 스냅샷 (일일 초기화)
            self._daily = DailyStats()
            await self._stage0_snapshot_balance()

            # [1단계] 15분봉 POI 설정
            await self._stage1_setup_poi()

            # 백그라운드 태스크 시작
            kill_task    = asyncio.create_task(self._kill_switch_loop())
            standby_task = asyncio.create_task(self._standby_recalc_loop())
            close_task   = asyncio.create_task(self._market_close_watcher())

            # 웹소켓 구독 요청 목록 생성
            ws_requests = [
                self._api.get_delayed_ccnl_req(self.symbol),
                self._api.get_ccnl_notice_req(self.hts_id),
            ]

            logger.info("웹소켓 수신 루프 시작")
            self._state = BotState.MONITORING

            try:
                await self._api.connect_and_listen_ws(ws_requests, self._ws_callback)
            except Exception as e:
                logger.error(f"웹소켓 도중 오류: {e}")
            finally:
                # 웹소켓 끝 나면 백그라운드 태스크 정리
                for t in [kill_task, standby_task, close_task]:
                    t.cancel()

            if self._state == BotState.SHUTDOWN:
                logger.warning("킬 스위치 발동 상태입니다. 차일 재시작도 중단합니다.")
                break

            logger.info("장 마감. 다음 장 오픈까지 대기합니다...")
            # 다음 날 장 오픈까지 다시 반복

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
        """15분봉 캔들을 조회하고 FVG/OB 구역을 탐지하여 POI로 설정합니다."""
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

        fvg_zones = detect_fvg(candles)
        ob_zones  = detect_ob(candles, fvg_zones)

        # 롱 전략이므로 Bullish(지지) 구역만 POI로 사용
        self._poi_zones = [z for z in (fvg_zones + ob_zones) if z["type"] == "bullish"]

        logger.info(f"[1단계] POI {len(self._poi_zones)}개 설정 완료")

    # ──────────────────────────────────────────────
    # [2단계] 웹소켓 콜백 - 실시간 POI 터치 감시
    # ──────────────────────────────────────────────

    async def _ws_callback(self, raw_data: str) -> None:
        """
        웹소켓에서 데이터가 수신될 때마다 호출되는 콜백 함수입니다.
        TR_ID에 따라 분기하여 처리합니다.
        """
        # KIS 웹소켓은 '|' 구분자로 데이터를 전송합니다.
        if not raw_data or raw_data.startswith("{"):
            return  # 시스템 메시지(JSON) 무시

        parts = raw_data.split("|")
        if len(parts) < 4:
            return

        tr_id   = parts[1]
        tr_data = parts[3]

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
            # [2단계] POI 터치 여부 감시
            touched_zone = is_price_in_poi(price, self._poi_zones)
            if touched_zone:
                logger.info(f"[2단계] POI 터치! → STANDBY 전환 (price={price:.4f})")
                self._state = BotState.STANDBY

        elif self._state == BotState.IN_POSITION:
            # [5단계] TP/SL 감시 및 분할 청산
            await self._stage5_manage_position(price)

    # ──────────────────────────────────────────────
    # [2.5단계] Standby 재계산 루프 (5분 주기)
    # ──────────────────────────────────────────────

    async def _standby_recalc_loop(self) -> None:
        """
        STANDBY 상태에서 5분마다 최신 5분봉을 조회하여
        SL/TP/수량을 재계산하고, 확정 진입 신호를 확인합니다.
        """
        while self._running:
            await asyncio.sleep(STANDBY_RECALC_INTERVAL_SEC)

            if self._state != BotState.STANDBY:
                continue

            logger.info("[2.5단계] 5분봉 재분석 시작...")

            res = await asyncio.to_thread(
                self._api.get_time_itemchartprice,
                self.excd, self.symbol,
                "05",                          # 5분봉
                "1",
                str(ENTRY_CANDLE_COUNT_5M),
            )
            candles_5m = self._extract_candles(res)

            if not candles_5m:
                continue

            # 잔고 조회로 현재 전체 자본 확인
            bal_res  = await asyncio.to_thread(
                self._api.inquire_overseas_present_balance,
                self.acnt_no, self.acnt_prdt_cd
            )
            capital  = self._parse_balance(bal_res)

            # 현재 호가(진입 예정가) 조회
            price_res    = await asyncio.to_thread(self._api.get_price, self.excd, self.symbol)
            entry_price  = self._parse_current_price(price_res)

            if not entry_price:
                continue

            # SL, 수량, TP 계산
            sl_price = calc_sl_price(candles_5m, direction="long")
            if not sl_price or sl_price >= entry_price:
                logger.warning("[2.5단계] 유효하지 않은 SL, 대기 유지")
                continue

            qty  = calc_qty(capital, TRADE_RISK_RATIO, entry_price, sl_price)

            # 15분봉의 Bearish FVG를 TP2 2순위 후보로 전달
            fvg_15m      = detect_fvg(self._candles_15m)
            bearish_fvgs = [z for z in fvg_15m if z["type"] == "bearish"]

            tp1 = calc_tp1(entry_price, sl_price)
            tp2 = calc_tp2(entry_price, sl_price, self._candles_15m, bearish_fvgs)

            # 임시로 포지션 정보에 저장 (실제 진입 전까지 업데이트됨)
            self._pos.symbol      = self.symbol
            self._pos.excd        = self.excd
            self._pos.entry_price = entry_price
            self._pos.total_qty   = qty
            self._pos.remaining_qty = qty
            self._pos.sl_price    = sl_price
            self._pos.tp1_price   = tp1
            self._pos.tp2_price   = tp2

            logger.info(
                f"[2.5단계] 재계산 완료 → "
                f"진입가={entry_price:.4f}, SL={sl_price:.4f}, "
                f"TP1={tp1:.4f}, TP2={tp2:.4f}, 수량={qty}주"
            )

            # 5분봉 확정 신호 확인 후 진입
            if self._check_entry_signal(candles_5m):
                await self._stage3_enter()

    def _check_entry_signal(self, candles_5m: List[Dict[str, Any]]) -> bool:
        """
        5분봉 확정 진입 신호를 확인합니다.
        - 마지막 캔들이 양봉(close > open)인 경우 진입 신호로 판단합니다.
        - 이후 고도화 가능: 5분봉 FVG/OB 생성 여부 추가 확인 가능
        """
        if not candles_5m:
            return False
        last = candles_5m[-1]
        try:
            close = float(last.get("close", last.get("last", 0)))
            open_ = float(last.get("open", last.get("oprc", 0)))
            return close > open_
        except (ValueError, TypeError):
            return False

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
            self._state = BotState.MONITORING
            logger.info("[사이클 종료] 다음 매매 사이클 대기 시작 → MONITORING")

    # ──────────────────────────────────────────────
    # [내부 헬퍼 메서드]
    # ──────────────────────────────────────────────

    def _market_sell(self, qty: int, reason: str = "") -> None:
        """동기 방식으로 시장가 매도 주문을 실행합니다. (asyncio.to_thread 용)"""
        logger.info(f"매도 주문: {self.symbol} {qty}주 ({reason})")
        self._api.order_overseas_stock(
            ord_dvsn=SELL_DVSN,
            ovrs_excg_cd=self.excd,
            pdno=self.symbol,
            ft_ord_qty=str(qty),
            ft_ord_unpr3="0",  # 시장가
        )

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