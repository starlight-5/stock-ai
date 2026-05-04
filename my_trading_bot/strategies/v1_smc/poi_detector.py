# -*- coding: utf-8 -*-
"""
15분봉/5분봉 캔들 데이터를 분석하여
FVG(Fair Value Gap)와 OB(Order Block) 구역을 탐지하는 모듈입니다.

[FVG 탐지 원리]
  세 개의 연속된 캔들에서, 첫 번째 캔들의 고가(high)와
  세 번째 캔들의 저가(low) 사이에 두 번째 캔들이 완전히 위치하여
  가격이 채워지지 않은 '빈 공간'(Gap)이 발생한 경우 FVG로 정의합니다.

  - 상승 FVG (Bullish FVG): 가격이 위로 급등하며 생긴 갭 (매수 POI)
    → 조건: candle[2].low > candle[0].high
  - 하락 FVG (Bearish FVG): 가격이 아래로 급락하며 생긴 갭 (매도 POI)
    → 조건: candle[2].high < candle[0].low

[OB 탐지 원리]
  FVG를 생성한 직전의 반대 방향 캔들을 Order Block으로 정의합니다.
  상승 FVG: FVG 생성 직전 하락 캔들(close < open)의 저가~고가 구간
"""

import logging
from typing import List, Dict, Any, Optional, Tuple

from .params import FVG_MIN_SIZE_RATIO

logger = logging.getLogger(__name__)


def _parse_candle(raw: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """
    KIS API 응답의 캔들 데이터를 내부적으로 사용할 딕셔너리로 변환합니다.
    
    :param raw: KIS API에서 받은 캔들 하나의 원시 데이터
    :return: {'open', 'high', 'low', 'close'} 딕셔너리, 실패 시 None
    """
    try:
        return {
            "open":  float(raw.get("open",  raw.get("oprc", 0))),
            "high":  float(raw.get("high",  raw.get("hipr", 0))),
            "low":   float(raw.get("low",   raw.get("lopr", 0))),
            "close": float(raw.get("close", raw.get("last", 0))),
        }
    except (ValueError, TypeError) as e:
        logger.warning(f"캔들 파싱 실패: {e} | 원본: {raw}")
        return None


def detect_fvg(candles_raw: List[Dict[str, Any]], min_size_ratio: float = FVG_MIN_SIZE_RATIO) -> List[Dict[str, Any]]:
    """
    연속된 캔들 데이터에서 모든 FVG(Fair Value Gap) 구역을 탐지하여 반환합니다.

    :param candles_raw: KIS API 시세 응답의 캔들 리스트 (오래된 순서 → 최신 순서)
    :param min_size_ratio: 유효한 FVG로 인정할 최소 갭 크기 비율
    :return: 탐지된 FVG 구역 목록. 각 항목은 아래 구조를 가집니다:
             {
               'type': 'bullish' | 'bearish',  # FVG 방향
               'top': float,                   # 구역 상단 가격
               'bottom': float,                # 구역 하단 가격
               'mid': float,                   # 구역 중간값 (진입 기준)
               'index': int,                   # 탐지된 캔들 인덱스
             }
    """
    # 원시 데이터 파싱
    candles = [_parse_candle(c) for c in candles_raw]
    candles = [c for c in candles if c is not None]  # 파싱 실패 제거

    fvg_zones: List[Dict[str, Any]] = []

    # 3개의 캔들이 필요하므로 i는 1부터 len-1까지 탐색 (i가 두 번째 캔들)
    for i in range(1, len(candles) - 1):
        prev  = candles[i - 1]   # 첫 번째 캔들
        curr  = candles[i]       # 두 번째 캔들 (FVG를 만드는 중심 캔들)
        nxt   = candles[i + 1]   # 세 번째 캔들

        # ─── 상승 FVG 탐지 ───
        # 세 번째 캔들의 저가 > 첫 번째 캔들의 고가 → 위로 건너뜀
        if nxt["low"] > prev["high"]:
            gap_bottom = prev["high"]
            gap_top    = nxt["low"]
            gap_size   = gap_top - gap_bottom

            # 최소 갭 크기 필터: 너무 작은 갭은 노이즈로 처리
            if gap_size / prev["close"] >= min_size_ratio:
                fvg_zones.append({
                    "type":   "bullish",
                    "top":    gap_top,
                    "bottom": gap_bottom,
                    "mid":    (gap_top + gap_bottom) / 2,
                    "index":  i,
                })

        # ─── 하락 FVG 탐지 ───
        # 세 번째 캔들의 고가 < 첫 번째 캔들의 저가 → 아래로 건너뜀
        elif nxt["high"] < prev["low"]:
            gap_top    = prev["low"]
            gap_bottom = nxt["high"]
            gap_size   = gap_top - gap_bottom

            if gap_size / prev["close"] >= min_size_ratio:
                fvg_zones.append({
                    "type":   "bearish",
                    "top":    gap_top,
                    "bottom": gap_bottom,
                    "mid":    (gap_top + gap_bottom) / 2,
                    "index":  i,
                })

    logger.info(f"FVG 탐지 완료: {len(fvg_zones)}개 발견")
    return fvg_zones


def detect_ob(candles_raw: List[Dict[str, Any]], fvg_zones: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    탐지된 FVG를 기준으로 해당 FVG를 생성한 직전 캔들(Order Block)을 탐지합니다.
    
    - Bullish FVG 앞 → 하락 캔들(close < open)이 Bullish OB
    - Bearish FVG 앞 → 상승 캔들(close > open)이 Bearish OB

    :param candles_raw: KIS API 캔들 원시 데이터 리스트
    :param fvg_zones: detect_fvg()로 탐지된 FVG 구역 목록
    :return: 탐지된 OB 구역 목록
    """
    candles = [_parse_candle(c) for c in candles_raw]
    candles = [c for c in candles if c is not None]

    ob_zones: List[Dict[str, Any]] = []

    for fvg in fvg_zones:
        # FVG의 두 번째 캔들 인덱스가 i이므로, OB는 i-1(=첫 번째 캔들)의 바로 앞 캔들
        ob_index = fvg["index"] - 1
        if ob_index < 0:
            continue

        ob_candle = candles[ob_index]

        if fvg["type"] == "bullish":
            # Bullish FVG 직전의 하락 캔들 → Bullish OB (매수 지지 구역)
            if ob_candle["close"] < ob_candle["open"]:
                ob_zones.append({
                    "type":   "bullish",
                    "top":    ob_candle["high"],
                    "bottom": ob_candle["low"],
                    "mid":    (ob_candle["high"] + ob_candle["low"]) / 2,
                    "index":  ob_index,
                    "origin": "ob",
                })

        elif fvg["type"] == "bearish":
            # Bearish FVG 직전의 상승 캔들 → Bearish OB (매도 저항 구역)
            if ob_candle["close"] > ob_candle["open"]:
                ob_zones.append({
                    "type":   "bearish",
                    "top":    ob_candle["high"],
                    "bottom": ob_candle["low"],
                    "mid":    (ob_candle["high"] + ob_candle["low"]) / 2,
                    "index":  ob_index,
                    "origin": "ob",
                })

    logger.info(f"OB 탐지 완료: {len(ob_zones)}개 발견")
    return ob_zones


def is_price_in_poi(current_price: float, poi_zones: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    현재 가격이 어떤 POI(관심 구역) 안에 있는지 확인합니다.

    :param current_price: 현재 실시간 체결가
    :param poi_zones: FVG 또는 OB 구역 목록
    :return: 현재 가격이 들어간 첫 번째 POI 구역. 없으면 None.
    """
    for zone in poi_zones:
        if zone["bottom"] <= current_price <= zone["top"]:
            logger.info(
                f"POI 터치 감지! 가격={current_price:.4f} "
                f"| 구역 [{zone['bottom']:.4f} ~ {zone['top']:.4f}] (type={zone['type']})"
            )
            return zone
    return None


def find_nearest_liquidity(current_price: float, candles_raw: List[Dict[str, Any]]) -> Tuple[Optional[float], Optional[float]]:
    """
    현재 가격을 기준으로 가장 가까운 이전 고점/저점(유동성 풀)을 찾습니다.
    2차 TP 타겟 1순위로 활용됩니다.

    :param current_price: 현재 체결가
    :param candles_raw: 분봉 캔들 데이터 리스트 (오래된 순 → 최신 순)
    :return: (nearest_high, nearest_low) 형태의 튜플
    """
    candles = [_parse_candle(c) for c in candles_raw]
    candles = [c for c in candles if c is not None]

    if not candles:
        return None, None

    # 현재가보다 위에 있는 고점들 중 가장 가까운 고점
    highs_above = [c["high"] for c in candles if c["high"] > current_price]
    nearest_high = min(highs_above) if highs_above else None

    # 현재가보다 아래에 있는 저점들 중 가장 가까운 저점
    lows_below = [c["low"] for c in candles if c["low"] < current_price]
    nearest_low = max(lows_below) if lows_below else None

    return nearest_high, nearest_low
