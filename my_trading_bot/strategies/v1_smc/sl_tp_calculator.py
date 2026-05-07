# -*- coding: utf-8 -*-
"""
동적 SL(손절가), TP(익절가), 진입 수량을 계산하는 모듈입니다.

[핵심 공식]
  진입 수량 = (총 자본 × 2% 리스크 룰) / (진입가 - 손절가)
  1차 TP = 진입가 + (진입가 - 손절가) × 1.5배 + 수수료 보정
  2차 TP = 유동성 풀(이전 고점) > 반대 FVG 구간 > 고정 RR 1:2 순으로 선택
"""

import logging
import math
from typing import Optional, List, Dict, Any

from .params import (
    TP1_RR_RATIO,
    TP2_RR_RATIO_FALLBACK,
    COMMISSION_RATE,
)
from .poi_detector import find_nearest_liquidity

logger = logging.getLogger(__name__)


def calc_sl_price(candles_entry: List[Dict[str, Any]], direction: str = "long") -> Optional[float]:
    """
    진입 타임프레임(예: 1분봉)에서 신호 캔들(FVG/OB를 형성한 캔들)의 꼬리를 손절 라인으로 설정합니다.
    
    - 롱(매수) 방향: 신호 캔들의 저가(low)를 SL로 사용
    - 숏(매도) 방향: 신호 캔들의 고가(high)를 SL로 사용
    
    :param candles_entry: 진입 타임프레임 캔들 원시 데이터 리스트
    :param direction: 매매 방향 ('long' 또는 'short')
    :return: 손절가 (float), 계산 불가 시 None
    """
    if not candles_entry:
        logger.warning("SL 계산 실패: 캔들 데이터가 없습니다.")
        return None

    # 가장 최근 신호 캔들을 기준으로 사용 (리스트의 마지막 캔들)
    signal_candle = candles_entry[-1]
    try:
        if direction == "long":
            # 롱 진입: 최근 신호 캔들의 저가가 손절 라인
            sl = float(signal_candle.get("low", signal_candle.get("lopr", 0)))
        else:
            # 숏 진입: 최근 신호 캔들의 고가가 손절 라인
            sl = float(signal_candle.get("high", signal_candle.get("hipr", 0)))
        
        logger.info(f"SL 계산 완료: {sl:.4f} (방향={direction})")
        return sl
    except (ValueError, TypeError) as e:
        logger.error(f"SL 계산 중 오류 발생: {e}")
        return None


def calc_qty(total_capital: float, risk_ratio: float, entry_price: float, sl_price: float) -> int:
    """
    2% 리스크 룰에 따라 1회 매매에서 진입할 수량을 계산합니다.
    
    [공식] 진입 수량 = (총 자본 × 리스크 비율) / |진입가 - 손절가|
    
    :param total_capital: 현재 총 계좌 자본 (USD)
    :param risk_ratio: 1회 허용 손실 비율 (예: 0.02 = 2%)
    :param entry_price: 예상 진입 단가
    :param sl_price: 설정된 손절 단가
    :return: 계산된 진입 수량 (정수, 최소 1주)
    """
    risk_per_trade = total_capital * risk_ratio  # 이번 거래에서 최대 손실 허용 금액
    price_diff = abs(entry_price - sl_price)      # 진입가와 손절가의 차이

    if price_diff == 0:
        logger.error("SL과 진입가가 동일하여 수량을 계산할 수 없습니다.")
        return 1  # 안전하게 최소 수량 반환

    qty = risk_per_trade / price_diff
    qty_int = max(1, math.floor(qty))  # 소수점 버림 (보수적 수량), 최소 1주

    # 마진 버퍼 적용: 시장가 진입 시 슬리피지를 고려해 자본금의 95% 이하로만 매수 가능
    max_affordable_qty = math.floor((total_capital * 0.95) / entry_price)
    final_qty = min(qty_int, max_affordable_qty)

    if final_qty <= 0:
        logger.error(f"마진 버퍼 초과 또는 자금 부족으로 진입 수량이 0입니다. (자본금: {total_capital:.2f})")
        return 0

    logger.info(
        f"수량 계산 완료: {final_qty}주 "
        f"(자본={total_capital:.2f}, 마진한도={max_affordable_qty}주, 리스크금액={risk_per_trade:.2f}, "
        f"진입가={entry_price:.4f}, SL={sl_price:.4f})"
    )
    return final_qty


def calc_tp1(entry_price: float, sl_price: float, rr: float = TP1_RR_RATIO, commission: float = COMMISSION_RATE) -> float:
    """
    1차 익절 가격(TP1)을 계산합니다. 수수료를 반영하여 실질 RR을 보정합니다.
    
    [공식] TP1 = 진입가 + (진입가 - SL) × RR_배율 + 수수료 보정
    
    :param entry_price: 진입 단가
    :param sl_price: 손절 단가
    :param rr: 목표 RR 배율 (기본: 1.5)
    :param commission: 왕복 수수료율 (기본: 0.5%)
    :return: 수수료 반영 1차 익절 가격
    """
    risk_size = abs(entry_price - sl_price)        # 리스크 크기
    reward    = risk_size * rr                     # 목표 수익 크기
    
    # 수수료 보정: 왕복 수수료(진입 + 청산)를 목표 수익에 추가
    commission_cost = entry_price * commission
    
    tp1 = entry_price + reward + commission_cost
    logger.info(f"TP1 계산 완료: {tp1:.4f} (RR={rr}, 수수료={commission_cost:.4f})")
    return tp1


def calc_tp2(
    entry_price: float,
    sl_price: float,
    candles_poi: List[Dict[str, Any]],
    bearish_fvg_zones: Optional[List[Dict[str, Any]]] = None,
    fallback_rr: float = TP2_RR_RATIO_FALLBACK,
) -> float:
    """
    2차 익절 가격(TP2)을 계산합니다. 아래 우선순위로 목표가를 선정합니다:
    
    1순위: 현재가 위의 가장 가까운 유동성 풀 (이전 고점)
    2순위: 반대 방향(Bearish) FVG 구간의 하단
    3순위: 고정 RR 배율 (기본 1:2)
    
    :param entry_price: 진입 단가
    :param sl_price: 손절 단가
    :param candles_poi: POI 타임프레임(예: 5분봉) 캔들 데이터
    :param bearish_fvg_zones: 반대 방향(저항) FVG 구역 목록
    :param fallback_rr: 3순위 고정 RR 배율
    :return: 선택된 2차 익절 가격
    """
    risk_size = abs(entry_price - sl_price)

    # ── 1순위: 유동성 풀 (이전 고점) ──
    nearest_high, _ = find_nearest_liquidity(entry_price, candles_poi)
    if nearest_high and nearest_high > entry_price:
        # 유동성 풀이 TP1보다 충분히 높은 경우에만 채택 (RR 1.2 이상)
        if nearest_high >= entry_price + risk_size * 1.2:
            logger.info(f"TP2 선정 (1순위 유동성 풀): {nearest_high:.4f}")
            return nearest_high

    # ── 2순위: 반대 방향 FVG 하단 ──
    if bearish_fvg_zones:
        # 현재가 위에 있는 Bearish FVG 구역 중 가장 가까운 것
        candidates = [
            z for z in bearish_fvg_zones
            if z["type"] == "bearish" and z["bottom"] > entry_price
        ]
        if candidates:
            nearest_fvg = min(candidates, key=lambda z: z["bottom"])
            logger.info(f"TP2 선정 (2순위 반대 FVG 하단): {nearest_fvg['bottom']:.4f}")
            return nearest_fvg["bottom"]

    # ── 3순위: 고정 RR 배율 ──
    tp2_fallback = entry_price + risk_size * fallback_rr
    logger.info(f"TP2 선정 (3순위 고정 RR {fallback_rr}): {tp2_fallback:.4f}")
    return tp2_fallback
