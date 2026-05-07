# -*- coding: utf-8 -*-
import logging
import aiohttp
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

class AlpacaClient:
    """
    Alpaca Market Data API v2를 사용하여 과거 데이터를 조회하는 클라이언트입니다.
    """
    BASE_URL = "https://data.alpaca.markets/v2/stocks"

    def __init__(self, api_key: str, secret_key: str):
        self.api_key = api_key
        self.secret_key = secret_key
        self.headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret_key
        }

    async def get_historical_candles(self, symbol: str, timeframe: str = "15Min", limit: int = 100) -> List[Dict[str, Any]]:
        """
        Alpaca에서 과거 캔들 데이터를 조회합니다.
        :param symbol: 종목 코드 (예: AAPL)
        :param timeframe: 시간 단위 (1Min, 5Min, 15Min, 1Day 등)
        :param limit: 조회할 캔들 개수
        :return: KIS 규격과 유사한 캔들 리스트
        """
        url = f"{self.BASE_URL}/bars"
        
        # 최근 1~2일치 데이터를 충분히 가져오기 위해 start 시간 설정
        # (Alpaca는 UTC 기준)
        end = datetime.utcnow()
        start = end - timedelta(days=5)
        
        params = {
            "symbols": symbol,
            "timeframe": timeframe,
            "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limit": limit,
            "adjustment": "all",
            "feed": "sip",
            "sort": "desc" # 최신 데이터가 앞에 오도록
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self.headers, params=params) as resp:
                    if resp.status != 200:
                        logger.error(f"Alpaca 데이터 조회 실패: {resp.status} {await resp.text()}")
                        return []
                    
                    data = await resp.json()
                    bars = data.get("bars", {}).get(symbol, [])
                    
                    # KIS 규격(stck_prpr, stck_oprc, stck_hgpr, stck_lwpr)으로 변환
                    formatted_candles = []
                    for b in bars:
                        formatted_candles.append({
                            "stck_prpr": str(b["c"]), # 종가
                            "stck_oprc": str(b["o"]), # 시가
                            "stck_hgpr": str(b["h"]), # 고가
                            "stck_lwpr": str(b["l"]), # 저가
                            "stck_cntg_vol": str(b["v"]), # 거래량
                            "stck_bsop_date": b["t"][:10].replace("-", ""),
                            "t": b["t"] # 원본 시간
                        })
                    
                    # KIS는 보통 최신이 0번 인덱스이므로 desc 정렬 유지
                    return formatted_candles

        except Exception as e:
            logger.error(f"Alpaca API 호출 중 예외 발생: {e}")
            return []
