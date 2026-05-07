# -*- coding: utf-8 -*-
"""
Alpaca Market Data API를 사용하여 부족한 과거 캔들 데이터를 조회하는 모듈입니다.
KIS API의 과거 데이터 제한을 보완하기 위해 사용됩니다.
"""

import logging
import aiohttp
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

class AlpacaHandler:
    def __init__(self, api_key: str, secret_key: str):
        """
        :param api_key: Alpaca API Key ID
        :param secret_key: Alpaca API Secret Key
        """
        self.api_key = api_key
        self.secret_key = secret_key
        self.base_url = "https://data.alpaca.markets/v2/stocks"
        self.headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret_key,
            "Accept": "application/json"
        }

    async def get_historical_candles(self, symbol: str, timeframe: str = "15Min", limit: int = 100, start: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Alpaca API를 통해 과거 캔들 데이터를 조회합니다.
        
        :param symbol: 종목 코드 (예: AAPL)
        :param timeframe: 시간 단위 (1Min, 5Min, 15Min, 1Day 등)
        :param limit: 조회할 캔들 개수
        :param start: 시작 시간 (RFC3339 format, e.g. '2023-01-01T00:00:00Z')
        :return: KIS 호환 형식의 캔들 리스트 [{'open', 'high', 'low', 'close'}, ...]
        """
        if not self.api_key or "your_alpaca" in self.api_key:
            return []

        url = f"{self.base_url}/bars"
        params = {
            "symbols": symbol,
            "timeframe": timeframe,
            "limit": limit,
            "adjustment": "all",
            "feed": "sip"
        }
        if start:
            params["start"] = start

        try:
            async with aiohttp.ClientSession(headers=self.headers) as session:
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        logger.warning(f"[Alpaca] 데이터 조회 실패: {resp.status} | {await resp.text()}")
                        return []
                    
                    data = await resp.json()
                    bars = data.get("bars", {}).get(symbol, [])
                    
                    # KIS 호환 형식으로 변환 (오래된 순서 유지)
                    kis_candles = []
                    for b in bars:
                        kis_candles.append({
                            "open":  float(b["o"]),
                            "high":  float(b["h"]),
                            "low":   float(b["l"]),
                            "close": float(b["c"]),
                            "time":  b["t"]
                        })
                    
                    logger.info(f"[Alpaca] {symbol} {timeframe} 과거 데이터 {len(kis_candles)}개 로드 완료")
                    return kis_candles

        except Exception as e:
            logger.error(f"[Alpaca] 데이터 조회 중 오류 발생: {e}")
            return []
