# -*- coding: utf-8 -*-
"""
상위 거래량 및 거래대금 종목을 탐지하는 스캐너 모듈입니다.
KISApiHandler의 기존 함수들을 활용합니다.
"""

import logging
from typing import List, Set, Dict, Any, Tuple
from .api_handler import KISApiHandler

logger = logging.getLogger(__name__)

# KIS API 응답에서 종목 코드를 가져오기 위해 시도할 키 이름 목록 (우선순위 순)
_SYMBOL_KEYS = ["symb", "rsym", "symbol", "SYMB", "RSYM"]

class RankScanner:
    def __init__(self, api: KISApiHandler):
        self._api = api

    def _extract_symbol(self, item: Dict) -> str:
        """
        API 응답 항목에서 종목 코드를 추출합니다.
        KIS API 버전에 따라 필드명이 다를 수 있어 여러 키를 순서대로 시도합니다.
        """
        for key in _SYMBOL_KEYS:
            val = item.get(key)
            if val:
                return val
        return ""

    def get_top_symbols(self, limit: int = 5) -> Set[Tuple[str, str]]:
        """
        거래량 상위 및 거래대금 상위 종목을 합쳐서 반환합니다.
        나스닥(NAS), 뉴욕(NYS), 아멕스(AMS) 거래소를 모두 스캔합니다.
        
        :param limit: 각 거래소 및 부문별 상위 개수
        :return: 중복이 제거된 (거래소코드, 종목코드) 튜플 집합
        """
        symbols: Set[Tuple[str, str]] = set()
        
        for excd in ["NAS", "NYS", "AMS"]:
            try:
                # 1. 거래량 상위 조회
                vol_res = self._api.get_trade_vol(excd=excd)
                logger.debug(f"[Scanner] {excd} 거래량 응답: {str(vol_res)[:300]}")
                vol_list = vol_res.get("output", vol_res.get("output1", []))
                for item in vol_list[:limit]:
                    symbol = self._extract_symbol(item)
                    if symbol:
                        symbols.add((excd, symbol))
                        
                # 2. 거래대금 상위 조회
                pbmn_res = self._api.get_trade_pbmn(excd=excd)
                logger.debug(f"[Scanner] {excd} 거래대금 응답: {str(pbmn_res)[:300]}")
                pbmn_list = pbmn_res.get("output", pbmn_res.get("output1", []))
                for item in pbmn_list[:limit]:
                    symbol = self._extract_symbol(item)
                    if symbol:
                        symbols.add((excd, symbol))
                
            except Exception as e:
                logger.error(f"[Scanner] {excd} 종목 스캔 중 오류 발생: {e}")
                
        logger.info(f"[Scanner] 탐지된 상위 종목 수: {len(symbols)}개")
        if symbols:
            logger.info(f"[Scanner] 탐지된 종목: {symbols}")
        return symbols
