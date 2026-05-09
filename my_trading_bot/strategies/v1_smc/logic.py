import asyncio
import logging
import time
import math
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field

import pandas as pd
import numpy as np

from my_trading_bot.core.constants import BotState
from my_trading_bot.utils.market_schedule import is_market_open, get_next_market_open

logger = logging.getLogger("V1SmcBot")

# ──────────────────────────────────────────────
# [전략 설정 상수]
# ──────────────────────────────────────────────
POI_CANDLE_COUNT = 100    # POI(OB/FVG) 탐색을 위한 5분봉 캔들 수
ENTRY_CANDLE_COUNT = 50   # 진입 타점 확인을 위한 1분봉 캔들 수
ATR_PERIOD = 14           # ATR 계산 기간
ATR_SL_MULT = 1.5         # ATR 기반 손절매(SL) 배수
TRADE_RISK_RATIO = 0.01   # 1회 트레이딩 시 총자산 대비 리스크 (1%)
RR_RATIO_TP1 = 1.5        # TP1 익절 손익비 (1.5:1)
RR_RATIO_TP2 = 3.0        # TP2 익절 손익비 (3.0:1)
AI_PROB_THRESHOLD = 0.6   # AI 필터 진입 승인 확률 임계치
STANDBY_RECALC_INTERVAL_SEC = 60 # STANDBY 상태에서 진입 POI 재계산 주기

# ──────────────────────────────────────────────
# [데이터 모델]
# ──────────────────────────────────────────────

@dataclass
class PositionInfo:
    """현재 보유 중인 포지션 및 주문 정보를 관리합니다."""
    symbol: str = ""
    excd: str = ""
    order_no: str = ""
    entry_price: float = 0.0
    total_qty: int = 0
    remaining_qty: int = 0
    sl_price: float = 0.0
    tp1_price: float = 0.0
    tp2_price: float = 0.0
    tp1_hit: bool = False

@dataclass
class DailyStats:
    """당일 트레이딩 성과 및 제한 사항을 관리합니다."""
    start_balance: float = 0.0
    current_balance: float = 0.0
    trade_count: int = 0
    max_trades_per_day: int = 3
    daily_drawdown_limit: float = -0.02 # -2% 손실 시 중단

    @property
    def drawdown_ratio(self) -> float:
        if self.start_balance <= 0: return 0.0
        return (self.current_balance - self.start_balance) / self.start_balance

# ──────────────────────────────────────────────
# [전략 핵심 로직 함수 (Pure Functions)]
# ──────────────────────────────────────────────

def is_price_in_poi(price: float, poi_zones: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """현재가가 주어진 POI 구역 내에 있는지 확인합니다."""
    for zone in poi_zones:
        if zone["low"] <= price <= zone["high"]:
            return zone
    return None

def calculate_atr(candles: List[Dict[str, Any]], period: int = 14) -> float:
    """캔들 데이터를 기반으로 ATR을 계산합니다."""
    if len(candles) < period + 1: return 0.0
    tr_list = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i-1]["close"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        tr_list.append(tr)
    return float(np.mean(tr_list[-period:]))

def detect_fvg(candles: List[Dict[str, Any]], atr: float = 0.0) -> List[Dict[str, Any]]:
    """Fair Value Gap (FVG) 구역을 탐지합니다."""
    fvgs = []
    min_size = atr * 0.1 if atr > 0 else 0
    for i in range(2, len(candles)):
        if candles[i-2]["high"] < candles[i]["low"]:
            size = candles[i]["low"] - candles[i-2]["high"]
            if size > min_size:
                fvgs.append({"type": "bullish", "type_label": "FVG(Bull)", "low": candles[i-2]["high"], "high": candles[i]["low"], "size": size, "index": i-1})
        elif candles[i-2]["low"] > candles[i]["high"]:
            size = candles[i-2]["low"] - candles[i]["high"]
            if size > min_size:
                fvgs.append({"type": "bearish", "type_label": "FVG(Bear)", "low": candles[i]["high"], "high": candles[i-2]["low"], "size": size, "index": i-1})
    return fvgs

def detect_ob(candles: List[Dict[str, Any]], fvgs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Order Block (OB) 구역을 탐지합니다."""
    obs = []
    fvg_indices = [f["index"] for f in fvgs]
    for i in range(1, len(candles) - 1):
        if i + 1 in fvg_indices:
            if candles[i]["close"] < candles[i]["open"]:
                obs.append({"type": "bullish", "type_label": "OB(Bull)", "low": candles[i]["low"], "high": candles[i]["high"], "index": i})
            elif candles[i]["close"] > candles[i]["open"]:
                obs.append({"type": "bearish", "type_label": "OB(Bear)", "low": candles[i]["low"], "high": candles[i]["high"], "index": i})
    return obs

def calc_sl_price(candles: List[Dict[str, Any]], direction: str = "long") -> Optional[float]:
    """최근 저점/고점 기반 손절가 계산"""
    if not candles: return None
    if direction == "long":
        return min([c["low"] for c in candles[-5:]]) * 0.998
    else:
        return max([c["high"] for c in candles[-5:]]) * 1.002

def calc_qty(capital: float, risk_ratio: float, entry: float, sl: float) -> int:
    """리스크 기반 진입 수량 계산"""
    risk_amt = capital * risk_ratio
    sl_dist = abs(entry - sl)
    if sl_dist <= 0: return 0
    return int(risk_amt / sl_dist)

def calc_tp1(entry: float, sl: float) -> float:
    return entry + (entry - sl) * RR_RATIO_TP1

def calc_tp2(entry: float, sl: float, candles: List[Dict[str, Any]], bearish_fvgs: List[Dict[str, Any]]) -> float:
    rr_tp2 = entry + (entry - sl) * RR_RATIO_TP2
    if bearish_fvgs:
        targets = [f["low"] for f in bearish_fvgs if f["low"] > entry]
        if targets: return min(min(targets), rr_tp2)
    return rr_tp2

# ──────────────────────────────────────────────
# [V1SmcBot 클래스]
# ──────────────────────────────────────────────

class V1SmcBot:
    """
    SMC(Smart Money Concepts) 전략 기반 트레이딩 봇
    국내/해외 주식 멀티 마켓 지원 버전
    """
    def __init__(self, api, symbol: str, excd: str = "NASD", market_type: str = "US", ai_model=None, on_state_change=None, on_trade=None):
        self._api = api
        self.symbol = symbol
        self.excd = excd
        self.market_type = market_type
        self._ai_model = ai_model
        self.on_state_change = on_state_change
        self.on_trade = on_trade
        
        self.acnt_no = ""
        self.acnt_prdt_cd = ""
        
        self._state = BotState.INITIALIZING
        self._running = False
        self._is_ordering = False
        
        self._pos = PositionInfo()
        self._daily = DailyStats()
        self._candles_poi = []
        self._candles_entry = []
        self._poi_zones = []
        self._poi_zones_entry = []
        self._active_poi = None

    async def setup(self) -> None:
        """봇 실행 초기화"""
        self._running = True
        self._daily = DailyStats()
        await self._stage0_snapshot_balance()
        await asyncio.sleep(0.5)
        await self._stage1_setup_poi()

    async def process_ws_data(self, tr_id: str, tr_data: str) -> None:
        """웹소켓 데이터 처리"""
        if self._state == BotState.SHUTDOWN: return
        if tr_id in ["HDFSCNT0", "H0STCNT0"]:
            price = self._parse_realtime_price(tr_data, tr_id)
            if price: await self._handle_price_update(price)
        elif tr_id in ["H0GSCNI0", "H0STCNI0", "H0STCNI9"]:
            await self._stage4_confirm_fill(tr_data, tr_id)

    async def _handle_price_update(self, price: float) -> None:
        """가격 업데이트 처리"""
        if self._state == BotState.SHUTDOWN: return
        if self._state == BotState.MONITORING:
            touched = is_price_in_poi(price, self._poi_zones)
            if touched:
                logger.info(f"[2단계] 5분봉 POI 터치! ({touched['type_label']}) -> STANDBY")
                self._state = BotState.STANDBY
                self._active_poi = touched
                if self.on_state_change: await self.on_state_change(self.symbol, self._state.value)
                await self._update_entry_poi()
        elif self._state == BotState.STANDBY:
            if self._active_poi and price > self._active_poi["high"] * 1.01:
                logger.info(f"[상태 복구] POI 이탈 -> MONITORING")
                self._state = BotState.MONITORING
                self._active_poi = None
                if self.on_state_change: await self.on_state_change(self.symbol, self._state.value)
                return
            if not self._is_ordering and self._poi_zones_entry:
                touched_entry = is_price_in_poi(price, self._poi_zones_entry)
                if touched_entry:
                    await self._execute_entry(price)
        elif self._state == BotState.IN_POSITION:
            await self._stage5_manage_position(price)

    async def _stage0_snapshot_balance(self) -> None:
        """잔고 스냅샷"""
        try:
            if self.market_type == "KR":
                res = await asyncio.to_thread(self._api.inquire_domestic_balance, self.acnt_no, self.acnt_prdt_cd)
            else:
                res = await asyncio.to_thread(self._api.inquire_overseas_present_balance, self.acnt_no, self.acnt_prdt_cd)
            balance = self._parse_balance(res)
            self._daily.start_balance = balance
            self._daily.current_balance = balance
            logger.info(f"[0단계] 잔고 스냅샷: {balance:,.0f}")
            self._state = BotState.MONITORING
        except Exception as e:
            logger.error(f"[0단계 에러] {e}")
            self._state = BotState.ERROR

    async def _stage1_setup_poi(self) -> None:
        """5분봉 POI 설정"""
        if self.market_type == "KR":
            res = await asyncio.to_thread(self._api.get_domestic_minute_chart, self.symbol, "300")
        else:
            res = await asyncio.to_thread(self._api.get_time_itemchartprice, self.excd, self.symbol, "01", "5", str(POI_CANDLE_COUNT))
        candles = self._extract_candles(res)
        if not candles: return
        self._candles_poi = candles
        atr = calculate_atr(candles)
        fvgs = detect_fvg(candles, atr)
        obs = detect_ob(candles, fvgs)
        self._poi_zones = [z for z in (fvgs + obs) if z["type"] == "bullish"]
        logger.info(f"[1단계] POI 탐지 완료")

    async def _update_entry_poi(self) -> None:
        """1분봉 POI 최신화"""
        if self.market_type == "KR":
            res = await asyncio.to_thread(self._api.get_domestic_minute_chart, self.symbol, "60")
        else:
            res = await asyncio.to_thread(self._api.get_time_itemchartprice, self.excd, self.symbol, "01", "1", str(ENTRY_CANDLE_COUNT))
        candles = self._extract_candles(res)
        if not candles: return
        self._candles_entry = candles
        atr = calculate_atr(candles)
        fvg = detect_fvg(candles, atr)
        ob = detect_ob(candles, fvg)
        self._poi_zones_entry = [z for z in (fvg + ob) if z["type"] == "bullish"]

    async def _execute_entry(self, entry_price: float) -> None:
        """진입 실행"""
        if self._is_ordering: return
        self._is_ordering = True
        try:
            sl_price = calc_sl_price(self._candles_entry, "long")
            if not sl_price or sl_price >= entry_price:
                self._is_ordering = False
                return
            qty = calc_qty(self._daily.current_balance, TRADE_RISK_RATIO, entry_price, sl_price)
            if qty <= 0:
                self._is_ordering = False
                return
            tp1 = calc_tp1(entry_price, sl_price)
            tp2 = calc_tp2(entry_price, sl_price, self._candles_poi, [z for z in self._poi_zones if z["type"] == "bearish"])
            self._pos = PositionInfo(symbol=self.symbol, excd=self.excd, entry_price=entry_price, total_qty=qty, remaining_qty=qty, sl_price=sl_price, tp1_price=tp1, tp2_price=tp2)
            await self._stage3_enter()
        except Exception as e:
            logger.error(f"[진입 에러] {e}")
            self._is_ordering = False

    async def _stage3_enter(self) -> None:
        """주문 전송"""
        if self.market_type == "KR":
            res = await asyncio.to_thread(self._api.order_domestic_stock, "01", self.symbol, str(self._pos.total_qty), "0")
        else:
            res = await asyncio.to_thread(self._api.order_overseas_stock, "02", self.excd, self.symbol, str(self._pos.total_qty), "0")
        order_no = res.get("output", {}).get("odno", "")
        self._pos.order_no = order_no
        logger.info(f"[3단계] 주문 전송: {order_no}")

    async def _stage4_confirm_fill(self, tr_data: str, tr_id: str) -> None:
        """체결 확인"""
        if self._state != BotState.STANDBY: return
        if tr_id == "H0GSCNI0":
            fields = tr_data.split("^")
            filled = fields[2] == "2" if len(fields) > 2 else False
        else:
            fields = tr_data.split("|")
            filled = fields[13] == "2" if len(fields) > 13 else False
        if filled:
            logger.info("[4단계] 체결 확인")
            self._state = BotState.IN_POSITION
            if self.on_state_change: await self.on_state_change(self.symbol, self._state.value)

    async def _stage5_manage_position(self, price: float) -> None:
        """포지션 관리"""
        pos = self._pos
        if not pos.tp1_hit and price >= pos.tp1_price:
            logger.info(f"[5단계] TP1 도달")
            sell_qty = max(1, int(pos.remaining_qty * 0.5))
            await asyncio.to_thread(self._market_sell, sell_qty, "TP1")
            pos.remaining_qty -= sell_qty
            pos.tp1_hit = True
            pos.sl_price = pos.entry_price
        elif pos.tp1_hit and price >= pos.tp2_price:
            logger.info(f"[5단계] TP2 도달")
            await asyncio.to_thread(self._market_sell, pos.remaining_qty, "TP2")
            await self._end_cycle()
        elif price <= pos.sl_price:
            logger.info(f"[5단계] SL 터치")
            await asyncio.to_thread(self._market_sell, pos.remaining_qty, "SL")
            await self._end_cycle()

    async def _end_cycle(self) -> None:
        """사이클 종료"""
        self._state = BotState.COOLDOWN
        if self.market_type == "KR":
            res = await asyncio.to_thread(self._api.inquire_domestic_balance, self.acnt_no, self.acnt_prdt_cd)
        else:
            res = await asyncio.to_thread(self._api.inquire_overseas_present_balance, self.acnt_no, self.acnt_prdt_cd)
        self._daily.current_balance = self._parse_balance(res)
        self._daily.trade_count += 1
        self._pos = PositionInfo()
        self._is_ordering = False
        self._state = BotState.MONITORING

    def _market_sell(self, qty: int, reason: str = "") -> None:
        """시장가 매도"""
        if qty <= 0: return
        for attempt in range(1, 4):
            try:
                if self.market_type == "KR":
                    res = self._api.order_domestic_stock("01", self.symbol, str(qty), "0")
                else:
                    res = self._api.order_overseas_stock("02", self.excd, self.symbol, str(qty), "0")
                if res.get("rt_cd") == "0": break
            except: pass
            time.sleep(1)

    def _calculate_indicators(self, candles: List[Dict[str, Any]]) -> Optional[Dict[str, float]]:
        """지표 계산"""
        if len(candles) < 20: return None
        try:
            closes = np.array([float(c.get("close", 0)) for c in candles])
            atr = calculate_atr(candles)
            ma20 = np.mean(closes[-20:])
            return {"atr_5m": atr, "rsi_5m": 50.0, "disparity_5m": float(closes[-1] / ma20) if ma20 > 0 else 1.0, "fvg_size_ratio": 0.01, "volume_ma_ratio": 1.0}
        except: return None

    def _extract_candles(self, res: Dict[str, Any]) -> List[Dict[str, Any]]:
        """캔들 추출"""
        output = res.get("output1", res.get("output2", res.get("output", [])))
        if not isinstance(output, list): return []
        normalized = []
        for c in output:
            try:
                normalized.append({"open": float(c.get("stck_oprc", c.get("open", 0))), "high": float(c.get("stck_hgpr", c.get("high", 0))), "low": float(c.get("stck_lwpr", c.get("low", 0))), "close": float(c.get("stck_clpr", c.get("close", 0))), "volume": float(c.get("cntg_vol", c.get("tvol", 0))), "time": c.get("stck_cntg_hour", c.get("time", ""))})
            except: continue
        return sorted(normalized, key=lambda x: x["time"])

    def _parse_balance(self, res: Dict[str, Any]) -> float:
        """잔고 파싱"""
        if not res: return 0.0
        try:
            asset_keys = ["tot_asst_amt", "pchs_amt_smtl", "tot_evlu_amt", "tot_frcr_cblc_smtl", "tot_dncl_amt"]
            def _to_float(val: Any) -> float:
                if val is None: return 0.0
                if isinstance(val, (int, float)): return float(val)
                s_val = str(val).replace(",", "").strip()
                try: return float(s_val) if s_val else 0.0
                except: return 0.0
            for out_key in ["output3", "output2", "output", "output1"]:
                out_data = res.get(out_key)
                if not out_data: continue
                data = out_data[0] if isinstance(out_data, list) and out_data else out_data
                if isinstance(data, dict):
                    for key in asset_keys:
                        val = _to_float(data.get(key))
                        if val > 0: return val
            return 0.0
        except: return 0.0

    def _parse_realtime_price(self, tr_data: str, tr_id: str) -> Optional[float]:
        """웹소켓 가격 파싱"""
        try:
            if tr_id == "HDFSCNT0":
                fields = tr_data.split("^")
                return float(fields[11]) if len(fields) > 11 else None
            else:
                fields = tr_data.split("|")
                return float(fields[2]) if len(fields) > 2 else None
        except: return None

    def _parse_current_price(self, res: Dict[str, Any]) -> Optional[float]:
        """현재가 파싱"""
        try:
            out = res.get("output", {})
            price = out.get("last") or out.get("stck_prpr")
            return float(price) if price else None
        except: return None
