# -*- coding: utf-8 -*-
"""
상위 거래량 및 거래대금 종목을 탐지하는 스캐너 모듈입니다.
KISApiHandler의 기존 함수들을 활용합니다.
"""

import logging
from typing import List, Set, Dict, Any, Tuple
from .api_handler import KISApiHandler

logger = logging.getLogger(__name__)

class RankScanner:
    def __init__(self, api: KISApiHandler):
        self._api = api

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
                vol_list = vol_res.get("output", [])
                for item in vol_list[:limit]:
                    symbol = item.get("symb")
                    if symbol:
                        symbols.add((excd, symbol))
                        
                # 2. 거래대금 상위 조회
                pbmn_res = self._api.get_trade_pbmn(excd=excd)
                pbmn_list = pbmn_res.get("output", [])
                for item in pbmn_list[:limit]:
                    symbol = item.get("symb")
                    if symbol:
                        symbols.add((excd, symbol))
                
            except Exception as e:
                logger.error(f"[Scanner] {excd} 종목 스캔 중 오류 발생: {e}")
                
        logger.info(f"[Scanner] 탐지된 상위 종목 수: {len(symbols)}개")
        return symbols
