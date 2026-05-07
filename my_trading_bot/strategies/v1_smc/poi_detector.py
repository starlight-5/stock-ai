# -*- coding: utf-8 -*-
"""
5분봉/1분봉 캔들 데이터를 분석하여
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

from .params import (
    FVG_MIN_SIZE_RATIO, FVG_ATR_MULTIPLIER, ATR_PERIOD,
    OB_DOJI_BODY_RATIO, OB_DOJI_WICK_RATIO,
)

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


def calculate_atr(candles: List[Dict[str, float]], period: int = ATR_PERIOD) -> Optional[float]:
    """
    ATR(Average True Range, 평균 실제 범위)을 계산합니다.

    True Range(TR) = max(
        high - low,
        |high - prev_close|,
        |low  - prev_close|
    )
    ATR = TR의 단순 이동평균 (EMA 대신 SMA 사용, 안정성 우선)

    :param candles: _parse_candle()로 파싱된 캔들 리스트 (오래된 → 최신)
    :param period:  ATR 평균 계산 기간 (기본 14)
    :return: ATR 값. 캔들 부족 시 None 반환
    """
    if len(candles) < period + 1:
        logger.debug(f"ATR 계산 불가: 캔들 {len(candles)}개 < 필요 {period + 1}개")
        return None

    tr_list: List[float] = []
    for i in range(1, len(candles)):
        high       = candles[i]["high"]
        low        = candles[i]["low"]
        prev_close = candles[i - 1]["close"]
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low  - prev_close),
        )
        tr_list.append(tr)

    # 최근 period 개의 TR 평균
    atr = sum(tr_list[-period:]) / period
    logger.debug(f"ATR({period}) = {atr:.6f}")
    return atr
def detect_fvg(
    candles_raw: List[Dict[str, Any]],
    atr: Optional[float] = None,
    atr_multiplier: float = FVG_ATR_MULTIPLIER,
    min_size_ratio: float = FVG_MIN_SIZE_RATIO,
) -> List[Dict[str, Any]]:
    """
    연속된 캔들 데이터에서 모든 FVG(Fair Value Gap) 구역을 탐지하여 반환합니다.

    [최소 갭 크기 판단 방식]
      - ATR 제공 시 : gap_size >= ATR × atr_multiplier  (동적 필터, 권장)
      - ATR 미제공 시: gap_size / prev_close >= min_size_ratio  (고정 비율 fallback)

    :param candles_raw:    KIS API 시세 응답의 캔들 리스트 (오래된 순서 → 최신 순서)
    :param atr:            사전 계산된 ATR 값. None 이면 고정 비율 fallback 사용.
    :param atr_multiplier: ATR 기반 필터의 배율 (기본 0.5 × ATR)
    :param min_size_ratio: ATR 미사용 시 적용할 고정 비율 기준 (기본 0.1%)
    :return: 탐지된 FVG 구역 목록. 각 항목:
             {
               'type': 'bullish' | 'bearish',
               'top': float, 'bottom': float, 'mid': float,
               'index': int, 'gap_size': float, 'atr': float|None
             }
    """
    candles = [_parse_candle(c) for c in candles_raw]
    candles = [c for c in candles if c is not None]

    use_atr  = atr is not None and atr > 0
    min_gap  = atr * atr_multiplier if use_atr else None
    filter_mode = (f"ATR×{atr_multiplier} ({atr:.5f})" if use_atr
                   else f"고정비율 {min_size_ratio:.4%}")
    logger.debug(f"FVG 필터 모드: {filter_mode}")

    fvg_zones: List[Dict[str, Any]] = []

    # 3개의 캔들이 필요하므로 i는 1부터 len-1까지 탐색 (i가 두 번째 캔들)
    for i in range(1, len(candles) - 1):
        prev = candles[i - 1]   # 첫 번째 캔들
        nxt  = candles[i + 1]   # 세 번째 캔들

        # ─── 상승 FVG: 세 번째 저가 > 첫 번째 고가 ───
        if nxt["low"] > prev["high"]:
            gap_bottom = prev["high"]
            gap_top    = nxt["low"]
            gap_size   = gap_top - gap_bottom

            valid = (gap_size >= min_gap) if use_atr else (gap_size / prev["close"] >= min_size_ratio)
            if valid:
                fvg_zones.append({
                    "type":       "bullish",
                    "type_label": "Bullish FVG",
                    "top":        gap_top,
                    "bottom":     gap_bottom,
                    "low":        gap_bottom, # logic.py 로깅용
                    "high":       gap_top,    # logic.py 로깅용
                    "mid":        (gap_top + gap_bottom) / 2,
                    "index":      i,
                    "origin":     "fvg",
                    "gap_size":   gap_size,
                    "atr":        atr,
                })

        # ─── 하락 FVG: 세 번째 고가 < 첫 번째 저가 ───
        elif nxt["high"] < prev["low"]:
            gap_top    = prev["low"]
            gap_bottom = nxt["high"]
            gap_size   = gap_top - gap_bottom

            valid = (gap_size >= min_gap) if use_atr else (gap_size / prev["close"] >= min_size_ratio)
            if valid:
                fvg_zones.append({
                    "type":       "bearish",
                    "type_label": "Bearish FVG",
                    "top":        gap_top,
                    "bottom":     gap_bottom,
                    "low":        gap_bottom, # logic.py 로깅용
                    "high":       gap_top,    # logic.py 로깅용
                    "mid":        (gap_top + gap_bottom) / 2,
                    "index":      i,
                    "origin":     "fvg",
                    "gap_size":   gap_size,
                    "atr":        atr,
                })

    logger.debug(f"FVG 탐지 완료: {len(fvg_zones)}개 발견 (필터: {filter_mode})")
    return fvg_zones



def _is_long_wick_doji(candle: Dict[str, float],
                        body_ratio: float = OB_DOJI_BODY_RATIO,
                        wick_ratio: float = OB_DOJI_WICK_RATIO) -> bool:
    """
    주어진 캔들이 '꼬리가 긴 도지 캔들'인지 판단합니다.

    조건:
      1) 몸통(실체) 크기가 전체 범위(high-low)의 body_ratio 이하일 것  ← 도지 조건
      2) 위 또는 아래 꼬리 길이 중 하나라도 몸통의 wick_ratio 배 이상일 것  ← 장꼬리 조건

    :param candle:     {'open', 'high', 'low', 'close'} 딕셔너리
    :param body_ratio: 몸통 / 전체 범위 기준 비율 (기본 0.3 = 30%)
    :param wick_ratio: 꼬리 / 몸통 기준 배수  (기본 2.0 = 2배)
    :return: 꼬리가 긴 도지이면 True
    """
    total_range = candle["high"] - candle["low"]
    if total_range == 0:
        return False

    body = abs(candle["close"] - candle["open"])

    # 조건 1: 몸통이 전체 범위의 body_ratio 이하여야 도지로 인정
    if body / total_range > body_ratio:
        return False

    # 조건 2: 위 또는 아래 꼬리 중 하나라도 몸통의 wick_ratio 배 이상
    upper_wick = candle["high"] - max(candle["open"], candle["close"])
    lower_wick = min(candle["open"], candle["close"]) - candle["low"]
    # 몸통이 0에 가까울 경우 전체 범위의 30%를 기준으로 삼아 나눗셈 오류 방지
    min_wick_len = body * wick_ratio if body > 0 else total_range * 0.3

    return upper_wick >= min_wick_len or lower_wick >= min_wick_len


def detect_ob(candles_raw: List[Dict[str, Any]], fvg_zones: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    탐지된 FVG를 기준으로 해당 FVG를 생성한 직전 캔들(Order Block)을 탐지합니다.

    OB 로 인정하는 직전 캔들 유형 (아래 중 하나 이상):
      a) 방향성 캔들:
         - Bullish FVG 앞 하락 캔들 (close < open)
         - Bearish FVG 앞 상승 캔들 (close > open)
      b) 꼬리가 긴 도지 캔들 (시세 거부 신호):
         - Bullish FVG 앞: 아래 꼬리가 긴 도지 (아래로 거부 → 매수 지지)
         - Bearish FVG 앞: 위 꼬리가 긴 도지 (위로 거부 → 매도 저항)

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
        is_doji = _is_long_wick_doji(ob_candle)

        if fvg["type"] == "bullish":
            # ─ a) 일반 하락 캔들 ─
            is_bearish_candle = ob_candle["close"] < ob_candle["open"]

            # ─ b) 아래 꼬리가 긴 도지: 아래로 거부 신호 (매수 지지)
            #     아래 꼬리 >= 위 꼬리 → 하락 거부 방향이 지배적
            lower_wick = min(ob_candle["open"], ob_candle["close"]) - ob_candle["low"]
            upper_wick = ob_candle["high"] - max(ob_candle["open"], ob_candle["close"])
            lower_wick_dominant = is_doji and (lower_wick >= upper_wick)

            if is_bearish_candle or lower_wick_dominant:
                ob_zones.append({
                    "type":       "bullish",
                    "type_label": "Bullish OB",
                    "top":        ob_candle["high"],
                    "bottom":     ob_candle["low"],
                    "low":        ob_candle["low"],  # logic.py 로깅용
                    "high":       ob_candle["high"], # logic.py 로깅용
                    "mid":        (ob_candle["high"] + ob_candle["low"]) / 2,
                    "index":      ob_index,
                    "origin":     "ob",
                    "is_doji":    is_doji,
                })

        elif fvg["type"] == "bearish":
            # ─ a) 일반 상승 캔들 ─
            is_bullish_candle = ob_candle["close"] > ob_candle["open"]

            # ─ b) 위 꼬리가 긴 도지: 위로 거부 신호 (매도 저항)
            #     위 꼬리 >= 아래 꼬리 → 상승 거부 방향이 지배적
            lower_wick = min(ob_candle["open"], ob_candle["close"]) - ob_candle["low"]
            upper_wick = ob_candle["high"] - max(ob_candle["open"], ob_candle["close"])
            upper_wick_dominant = is_doji and (upper_wick >= lower_wick)

            if is_bullish_candle or upper_wick_dominant:
                ob_zones.append({
                    "type":       "bearish",
                    "type_label": "Bearish OB",
                    "top":        ob_candle["high"],
                    "bottom":     ob_candle["low"],
                    "low":        ob_candle["low"],  # logic.py 로깅용
                    "high":       ob_candle["high"], # logic.py 로깅용
                    "mid":        (ob_candle["high"] + ob_candle["low"]) / 2,
                    "index":      ob_index,
                    "origin":     "ob",
                    "is_doji":    is_doji,
                })

    logger.debug(f"OB 탐지 완료: {len(ob_zones)}개 발견")
    return ob_zones



def is_overlapping(zone1: Dict[str, Any], zone2: Dict[str, Any]) -> bool:
    """
    두 구역(zone)이 서로 겹치는지(Overlap) 확인합니다.
    SMC 전략에서 상위 POI(5분봉)와 하위 POI(1분봉)의 유효 중첩을 판정할 때 사용합니다.

    공식: max(Bottom1, Bottom2) < min(Top1, Top2)
    """
    bottom1, top1 = zone1["bottom"], zone1["top"]
    bottom2, top2 = zone2["bottom"], zone2["top"]
    
    return max(bottom1, bottom2) < min(top1, top2)


def is_price_in_poi(current_price: float, poi_zones: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    현재 가격이 어떤 POI(관심 구역)를 터치하거나 그 안에 있는지 확인합니다.
    시장의 노이즈와 슬리피지를 고려하여 판정 기준을 완화(Penetration 기준)합니다.

    [판정 기준]
    - Bullish(매수): 현재가 <= 구역 상단(top)
    - Bearish(매도): 현재가 >= 구역 하단(bottom)

    :param current_price: 현재 실시간 체결가
    :param poi_zones: FVG 또는 OB 구역 목록
    :return: 현재 가격이 진입한 첫 번째 POI 구역. 없으면 None.
    """
    for zone in poi_zones:
        # Bullish(매수 구역)인 경우: 가격이 상단을 터치하거나 아래로 내려왔을 때
        if zone["type"] == "bullish":
            if current_price <= zone["top"]:
                logger.debug(
                    f"Bullish POI 터치! 가격={current_price:.4f} <= 상단={zone['top']:.4f} "
                    f"({zone['type_label']})"
                )
                return zone
        
        # Bearish(매도 구역)인 경우: 가격이 하단을 터치하거나 위로 올라왔을 때
        elif zone["type"] == "bearish":
            if current_price >= zone["bottom"]:
                logger.debug(
                    f"Bearish POI 터치! 가격={current_price:.4f} >= 하단={zone['bottom']:.4f} "
                    f"({zone['type_label']})"
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
