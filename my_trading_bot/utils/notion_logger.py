# -*- coding: utf-8 -*-
"""
Notion API를 사용하여 트레이딩 봇의 상태를 대시보드에 업데이트하는 모듈입니다.
데이터베이스(Database)를 활용하여 종목별 상태를 체계적으로 관리합니다.
"""

import os
import logging
import asyncio
from datetime import datetime
from typing import Dict, List, Any, Optional
import aiohttp

logger = logging.getLogger(__name__)

class NotionLogger:
    def __init__(self, token: str, page_id: str):
        """
        :param token: Notion API 통합 토큰
        :param page_id: 대시보드 페이지 ID
        """
        self.token = token
        self.page_id = page_id.replace("-", "") # 하이픈 제거된 ID 사용
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28"
        }
        self.base_url = "https://api.notion.com/v1"
        self.db_id: Optional[str] = None
        self.session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """세션이 없으면 생성하여 반환합니다."""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(headers=self.headers)
        return self.session

    async def close(self):
        """네트워크 세션을 안전하게 닫습니다."""
        if self.session and not self.session.closed:
            await self.session.close()
            logger.info("[Notion] 세션 종료 완료")

    async def initialize(self):
        """대시보드 구조 초기화 및 데이터베이스 연결을 확인합니다."""
        if not self.token or not self.page_id:
            logger.warning("[Notion] 토큰 또는 페이지 ID가 설정되지 않아 기능을 비활성화합니다.")
            return

        try:
            session = await self._get_session()
            # 1. 페이지 내 자식 블록 조회하여 데이터베이스가 있는지 확인
            url = f"{self.base_url}/blocks/{self.page_id}/children"
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.error(f"[Notion] 페이지 조회 실패: {await resp.text()}")
                    return
                data = await resp.json()
                blocks = data.get("results", [])

            for block in blocks:
                if block["type"] == "child_database":
                    if "종목 상태" in block["child_database"]["title"]:
                        self.db_id = block["id"]
                        logger.info(f"[Notion] 기존 데이터베이스 연결 성공: {self.db_id}")
                        break
            
            # 2. 데이터베이스가 없으면 생성
            if not self.db_id:
                await self._create_status_database(session)
                    
        except Exception as e:
            logger.error(f"[Notion] 초기화 중 오류 발생: {e}")

    async def _create_status_database(self, session):
        """페이지 내에 종목 상태 관리용 데이터베이스를 생성합니다."""
        url = f"{self.base_url}/databases"
        payload = {
            "parent": {"type": "page_id", "page_id": self.page_id},
            "title": [{"type": "text", "text": {"content": "실시간 종목 상태"}}],
            "properties": {
                "Name": {"title": {}}, # 종목명
                "Symbol": {"rich_text": {}},
                "State": {
                    "select": {
                        "options": [
                            {"name": "IDLE", "color": "gray"},
                            {"name": "MONITORING", "color": "blue"},
                            {"name": "STANDBY", "color": "yellow"},
                            {"name": "IN_POSITION", "color": "green"},
                            {"name": "COOLDOWN", "color": "purple"},
                            {"name": "SHUTDOWN", "color": "red"}
                        ]
                    }
                },
                "Position": {"number": {"format": "number"}},
                "EntryPrice": {"number": {"format": "dollar"}},
                "Update": {"last_edited_time": {}}
            }
        }
        async with session.post(url, json=payload) as resp:
            if resp.status == 200:
                data = await resp.json()
                self.db_id = data["id"]
                logger.info(f"[Notion] 새 데이터베이스 생성 완료: {self.db_id}")
            else:
                logger.error(f"[Notion] 데이터베이스 생성 실패: {await resp.text()}")

    async def update_symbol_status(self, symbol: str, state: str, position: int = 0, entry_price: float = 0.0):
        """특정 종목의 상태를 데이터베이스에 업데이트합니다."""
        if not self.db_id:
            return

        try:
            session = await self._get_session()
            # 1. 해당 종목의 행(Page)이 있는지 쿼리
            query_url = f"{self.base_url}/databases/{self.db_id}/query"
            query_payload = {
                "filter": {
                    "property": "Name",
                    "title": {"equals": symbol}
                }
            }
            async with session.post(query_url, json=query_payload) as resp:
                data = await resp.json()
                results = data.get("results", [])

            properties = {
                "State": {"select": {"name": state}},
                "Position": {"number": position},
                "EntryPrice": {"number": entry_price},
                "Symbol": {"rich_text": [{"text": {"content": symbol}}]}
            }

            if results:
                # 기존 행 업데이트
                page_id = results[0]["id"]
                update_url = f"{self.base_url}/pages/{page_id}"
                await session.patch(update_url, json={"properties": properties})
            else:
                # 새 행 추가
                create_url = f"{self.base_url}/pages"
                new_payload = {
                    "parent": {"database_id": self.db_id},
                    "properties": {
                        "Name": {"title": [{"text": {"content": symbol}}]},
                        **properties
                    }
                }
                await session.post(create_url, json=new_payload)
            
            # 요약 시간도 업데이트
            await self.update_summary_time(session)
                
        except Exception as e:
            logger.error(f"[Notion] 종목 상태 업데이트 중 오류: {e}")

    async def update_summary_time(self, session):
        """대시보드 상단의 마지막 업데이트 시간을 갱신합니다."""
        url = f"{self.base_url}/blocks/{self.page_id}/children"
        async with session.get(url) as resp:
            blocks = (await resp.json()).get("results", [])

        for block in blocks:
            if block["type"] == "paragraph":
                text = block["paragraph"]["rich_text"]
                if text and "마지막 업데이트" in text[0]["plain_text"]:
                    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    update_url = f"{self.base_url}/blocks/{block['id']}"
                    payload = {
                        "paragraph": {
                            "rich_text": [{"text": {"content": f"마지막 업데이트: {now_str} (KST)"}}]
                        }
                    }
                    await session.patch(update_url, json=payload)
                    break

    async def add_trade_log(self, symbol: str, trade_type: str, details: Dict[str, Any]):
        """최근 체결 내역 섹션에 새로운 로그를 추가합니다."""
        if not self.token or not self.page_id:
            return

        try:
            session = await self._get_session()
            now_str = datetime.now().strftime("%H:%M:%S")
            # details를 보기 좋게 문자열로 변환
            detail_str = f"가격: {details.get('price', 0):.2f}, 수량: {details.get('qty', 0)}"
            log_text = f"[{now_str}] {symbol:8} | {trade_type:10} | {detail_str}"
            
            url = f"{self.base_url}/blocks/{self.page_id}/children"
            payload = {
                "children": [
                    {
                        "bulleted_list_item": {
                            "rich_text": [
                                {"text": {"content": log_text}, "annotations": {"code": True}}
                            ]
                        }
                    }
                ]
            }
            async with session.patch(url, json=payload) as resp:
                if resp.status != 200:
                    logger.error(f"[Notion] 로그 추가 실패: {await resp.text()}")
        except Exception as e:
            logger.error(f"[Notion] 로그 추가 중 오류 발생: {e}")
