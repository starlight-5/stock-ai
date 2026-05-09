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

    def get_top_symbols(self, market_type: str = "US", limit: int = 5) -> Set[Tuple[str, str]]:
        """
        거래량 상위 및 거래대금 상위 종목을 합쳐서 반환합니다.
        
        :param market_type: "US" (나스닥, 뉴욕, 아멕스) 또는 "KR" (KOSPI)
        :param limit: 각 부문별 상위 개수
        :return: 중복이 제거된 (거래소코드, 종목코드) 튜플 집합
        """
        symbols: Set[Tuple[str, str]] = set()
        
        if market_type == "US":
            for excd in ["NAS", "NYS", "AMS"]:
                try:
                    # 1. 거래량 상위
                    vol_res = self._api.get_trade_vol(excd=excd)
                    for item in self._get_ranked_list(vol_res)[:limit*2]:
                        if len([s for e, s in symbols if e == excd]) >= limit: break
                        symbol = self._extract_symbol(item)
                        name = self._extract_name(item)
                        if "ETF" not in name and symbol:
                            symbols.add((excd, symbol))
                            
                    # 2. 거래대금 상위
                    pbmn_res = self._api.get_trade_pbmn(excd=excd)
                    for item in self._get_ranked_list(pbmn_res)[:limit*2]:
                        if len([s for e, s in symbols if e == excd]) >= limit: break
                        symbol = self._extract_symbol(item)
                        name = self._extract_name(item)
                        if "ETF" not in name and symbol:
                            symbols.add((excd, symbol))
                except Exception as e:
                    logger.error(f"[Scanner] {excd} 스캔 중 오류: {e}")
        
        elif market_type == "KR":
            # 국내 주식은 "J" (KRX) 시장 기준
            excd = "KR"
            try:
                # 1. 거래량 상위 (rank_type="0")
                vol_res = self._api.get_domestic_volume_rank(market="J", rank_type="0")
                for item in self._get_ranked_list(vol_res)[:limit*3]:
                    if len(symbols) >= limit: break
                    symbol = self._extract_symbol(item)
                    name = self._extract_name(item)
                    # 국내 ETF/ETN 필터링
                    if any(x in name for x in ["ETF", "ETN", "스팩"]) and symbol:
                        continue
                    if symbol:
                        symbols.add((excd, symbol))
                
                # 2. 거래대금 상위 (rank_type="3")
                amt_res = self._api.get_domestic_volume_rank(market="J", rank_type="3")
                for item in self._get_ranked_list(amt_res)[:limit*3]:
                    if len(symbols) >= limit*2: break
                    symbol = self._extract_symbol(item)
                    name = self._extract_name(item)
                    if any(x in name for x in ["ETF", "ETN", "스팩"]) and symbol:
                        continue
                    if symbol:
                        symbols.add((excd, symbol))
            except Exception as e:
                logger.error(f"[Scanner] KR 스캔 중 오류: {e}")
                
        logger.info(f"[Scanner] 탐지된 상위 종목 수: {len(symbols)}개")
        return symbols
