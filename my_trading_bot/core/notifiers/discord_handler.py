# -*- coding: utf-8 -*-
import logging
import aiohttp
import json
from typing import Dict, Any

logger = logging.getLogger(__name__)

class DiscordHandler:
    """
    Discord Webhook을 사용하여 알림을 전송하는 핸들러입니다.
    """
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    async def send_message(self, content: str, title: str = "🤖 Trading Bot Alert", color: int = 0x00ff00):
        """
        메시지를 Discord로 전송합니다.
        """
        if not self.webhook_url:
            logger.warning("Discord Webhook URL이 설정되지 않았습니다.")
            return

        payload = {
            "embeds": [{
                "title": title,
                "description": content,
                "color": color
            }]
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.webhook_url, json=payload) as resp:
                    if resp.status not in [200, 204]:
                        logger.error(f"Discord 알림 전송 실패: {resp.status} {await resp.text()}")
        except Exception as e:
            logger.error(f"Discord 알림 전송 중 예외 발생: {e}")
