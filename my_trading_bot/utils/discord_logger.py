# -*- coding: utf-8 -*-
"""
디스코드 웹훅을 사용하여 트레이딩 봇의 실시간 알림을 전송하는 모듈입니다.
"""

import logging
import aiohttp
from datetime import datetime
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class DiscordLogger:
    def __init__(self, webhook_url: str):
        """
        :param webhook_url: 디스코드 채널 웹훅 URL
        """
        self.webhook_url = webhook_url

    async def send_message(self, text: str):
        """단순 텍스트 메시지를 전송합니다."""
        if not self.webhook_url or "your_discord" in self.webhook_url:
            return

        payload = {"content": text}
        await self._post(payload)

    async def send_status_alert(self, title: str, message: str, color: int = 0x3498db):
        """시스템 상태 알림을 Embed 형태로 전송합니다."""
        embed = {
            "title": f"🔔 {title}",
            "description": message,
            "color": color,
            "timestamp": datetime.utcnow().isoformat()
        }
        await self._post({"embeds": [embed]})

    async def send_trade_alert(self, symbol: str, trade_type: str, details: Dict[str, Any]):
        """거래 발생 알림을 Embed 형태로 전송합니다."""
        color = 0x2ecc71 if "BUY" in trade_type or "TP" in trade_type else 0xe74c3c
        
        # 상세 정보 문자열 구성
        fields = []
        if "price" in details:
            fields.append({"name": "가격", "value": f"${details['price']:.2f}", "inline": True})
        if "qty" in details:
            fields.append({"name": "수량", "value": f"{details['qty']}주", "inline": True})
        if "order_no" in details:
            fields.append({"name": "주문번호", "value": details['order_no'], "inline": False})

        embed = {
            "title": f"🚀 [{symbol}] {trade_type}",
            "fields": fields,
            "color": color,
            "timestamp": datetime.utcnow().isoformat(),
            "footer": {"text": "SMC Trading Bot"}
        }
        await self._post({"embeds": [embed]})

    async def _post(self, payload: dict):
        """웹훅 URL로 POST 요청을 보냅니다."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.webhook_url, json=payload) as resp:
                    if resp.status not in [200, 204]:
                        logger.error(f"[Discord] 알림 전송 실패: {resp.status} | {await resp.text()}")
        except Exception as e:
            logger.error(f"[Discord] 전송 중 오류 발생: {e}")
