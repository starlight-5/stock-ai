# -*- coding: utf-8 -*-
"""
상위 거래량 및 거래대금 종목을 탐지하는 스캐너 모듈입니다.
국내(KR) 및 해외(US) 주식 시장을 모두 지원합니다.
"""

import logging
from typing import List, Set, Dict, Any, Tuple
from .api_handler import KISApiHandler

logger = logging.getLogger(__name__)

# KIS API 응답에서 종목 코드를 가져오기 위해 시도할 키 이름 목록
_SYMBOL_KEYS = ["symb", "rsym", "symbol", "SYMB", "RSYM", "mksc_shrn_iscd"]
_NAME_KEYS = ["name", "hname", "NAME", "HNAME", "knam", "hts_kanm"]

class RankScanner:
    def __init__(self, api: KISApiHandler):
        self._api = api

    def _extract_symbol(self, item: Dict) -> str:
        """API 응답 항목에서 종목 코드를 추출합니다."""
        for key in _SYMBOL_KEYS:
            val = item.get(key)
            if val: return val.strip()
        return ""

    def _extract_name(self, item: Dict) -> str:
        """API 응답 항목에서 종목명(한글/영문)을 추출합니다."""
        for key in _NAME_KEYS:
            val = item.get(key)
            if val: return val.strip().upper()
        return ""

    def _get_ranked_list(self, res: dict) -> list:
        """KIS 랭킹 API 응답에서 종목 목록을 안전하게 추출합니다."""
        result = res.get("output2") or res.get("output1") or res.get("output")
        if isinstance(result, list):
            return result
        return []

    def _get_atr_volatility(self, excd: str, symbol: str) -> float:
        """종목의 ATR(14) / 현재가 비율(%)을 계산합니다."""
        try:
            if excd == "KR":
                res = self._api.get_domestic_daily_price(symbol)
                # 국내 주식 일별 챠트 응답의 데이터는 output2에 위치
                candles = res.get("output2", [])
            else:
                res = self._api.get_dailyprice(excd, symbol)
                candles = res.get("output2", [])
            
            if len(candles) < 15: return 0.0
            
            # ATR 계산을 위한 True Range 추출
            trs = []
            # KIS API 응답은 최신순이므로 역순으로 보거나 인덱스 주의
            for i in range(min(len(candles) - 1, 20)):
                # 필드명 매핑 (국내/해외 공통 처리 시도)
                h = float(candles[i].get("stck_hgpr") or candles[i].get("high") or 0)
                l = float(candles[i].get("stck_lwpr") or candles[i].get("low") or 0)
                pc = float(candles[i+1].get("stck_clpr") or candles[i+1].get("clos") or candles[i+1].get("close") or 0)
                
                if h == 0 or l == 0: continue
                tr = max(h - l, abs(h - pc), abs(l - pc))
                trs.append(tr)
            
            if not trs: return 0.0
            atr = sum(trs[:14]) / len(trs[:14])
            curr_price = float(candles[0].get("stck_clpr") or candles[0].get("clos") or candles[0].get("close") or 1)
            
            vol_score = (atr / curr_price) * 100
            return vol_score
        except Exception as e:
            logger.debug(f"[Scanner] ATR 계산 실패 ({symbol}): {e}")
            return 0.0

    def get_top_symbols(self, market_type: str = "US", limit: int = 5, min_vol: float = 1.0) -> Set[Tuple[str, str]]:
        """
        거래량 상위 종목 중 변동성이 높은 종목을 합쳐서 반환합니다.
        
        :param market_type: "US" 또는 "KR"
        :param limit: 각 부문별 상위 개수
        :param min_vol: 최소 ATR 변동성 비율 (%) - 데이터 수집을 위해 1.0%로 완화
        :return: 중복이 제거된 (거래소코드, 종목코드) 튜플 집합
        """
        raw_symbols: Set[Tuple[str, str]] = set()
        
        # 1. 기초 후보 종목 수집 (거래량/거래대금 상위)
        if market_type == "US":
            for excd in ["NAS", "NYS", "AMS"]:
                try:
                    vol_res = self._api.get_trade_vol(excd=excd)
                    for item in self._get_ranked_list(vol_res)[:limit*3]:
                        symbol = self._extract_symbol(item)
                        name = self._extract_name(item)
                        if "ETF" not in name and symbol:
                            raw_symbols.add((excd, symbol))
                            
                    pbmn_res = self._api.get_trade_pbmn(excd=excd)
                    for item in self._get_ranked_list(pbmn_res)[:limit*3]:
                        symbol = self._extract_symbol(item)
                        name = self._extract_name(item)
                        if "ETF" not in name and symbol:
                            raw_symbols.add((excd, symbol))
                except Exception as e:
                    logger.error(f"[Scanner] {excd} 스캔 중 오류: {e}")
        
        elif market_type == "KR":
            excd = "KR"
            try:
                vol_res = self._api.get_domestic_volume_rank(market="J", rank_type="0")
                for item in self._get_ranked_list(vol_res)[:limit*5]:
                    symbol = self._extract_symbol(item)
                    name = self._extract_name(item)
                    if any(x in name for x in ["ETF", "ETN", "스팩", "우"]) and symbol: continue
                    if symbol: raw_symbols.add((excd, symbol))
                
                amt_res = self._api.get_domestic_volume_rank(market="J", rank_type="3")
                for item in self._get_ranked_list(amt_res)[:limit*5]:
                    symbol = self._extract_symbol(item)
                    name = self._extract_name(item)
                    if any(x in name for x in ["ETF", "ETN", "스팩", "우"]) and symbol: continue
                    if symbol: raw_symbols.add((excd, symbol))
            except Exception as e:
                logger.error(f"[Scanner] KR 스캔 중 오류: {e}")

        # 2. ATR 변동성 기반 2차 필터링
        final_symbols: Set[Tuple[str, str]] = set()
        logger.info(f"[Scanner] 후보 {len(raw_symbols)}개 종목 변동성 분석 시작 (기준: {min_vol}%)...")
        
        for excd, symbol in raw_symbols:
            vol_score = self._get_atr_volatility(excd, symbol)
            if vol_score >= min_vol:
                final_symbols.add((excd, symbol))
                if len(final_symbols) >= limit * 2: break # 목표 개수 채우면 중단
            
        logger.info(f"[Scanner] 최종 선정된 종목 수: {len(final_symbols)}개")
        return final_symbols
