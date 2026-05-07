# -*- coding: utf-8 -*-
"""
V1 SMC 다중 종목 자동매매 시스템 매니저
거래량/거래대금 상위 종목을 실시간 탐지하여 운용합니다.
"""

import asyncio
import logging
import os
import sys
import yaml
from typing import Dict, Set, List, Optional
from dotenv import load_dotenv, find_dotenv

# 프로젝트 루트 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from my_trading_bot.core.api_handler import KISApiHandler
from my_trading_bot.core.scanner import RankScanner
from my_trading_bot.strategies.v1_smc.logic import V1SmcBot
from my_trading_bot.utils.market_schedule import wait_for_market_open, is_market_open
from my_trading_bot.utils.notion_logger import NotionLogger
from my_trading_bot.utils.discord_logger import DiscordLogger
from my_trading_bot.core.alpaca_handler import AlpacaHandler

logger = logging.getLogger(__name__)

class SymbolManager:
    def __init__(self, api: KISApiHandler, config: dict, hts_id: str, acnt_no: str, acnt_prdt: str):
        self.api = api
        self.config = config
        self.hts_id = hts_id
        self.acnt_no = acnt_no
        self.acnt_prdt = acnt_prdt
        
        self.scanner = RankScanner(api)
        self.bots: Dict[str, V1SmcBot] = {}
        self.bot_tasks: Dict[str, List[asyncio.Task]] = {}
        
        # Notion 로거 설정
        notion_token = os.getenv("NOTION_TOKEN", "")
        notion_page_id = os.getenv("NOTION_PAGE_ID", "357acb9f-6166-81f7-b5f1-e6c15b36bcd0")
        self.notion = NotionLogger(notion_token, notion_page_id)
        
        # 디스코드 로거 설정
        discord_webhook = os.getenv("DISCORD_WEBHOOK_URL", "")
        self.discord = DiscordLogger(discord_webhook)

        # Alpaca 핸들러 설정
        alpaca_key = os.getenv("ALPACA_API_KEY", "")
        alpaca_secret = os.getenv("ALPACA_SECRET_KEY", "")
        self.alpaca = AlpacaHandler(alpaca_key, alpaca_secret)
        
        # 웹소켓 추가 구독을 위한 큐
        self.ws_queue = asyncio.Queue()
        self._running = False
        self._consecutive_errors = 0

    async def _ws_callback(self, raw_data: str):
        """중앙 웹소켓 콜백: 데이터를 각 봇으로 배분합니다."""
        if not raw_data or raw_data.startswith("{"):
            return

        parts = raw_data.split("|")
        if len(parts) < 4:
            return

        tr_id = parts[1]
        tr_data = parts[3]
        fields = tr_data.split("^")

        if tr_id == "HDFSCNT0":  # 실시간 체결가
            # fields[0]은 "D" + 종목코드 형식 (예: DAAPL)
            symbol = fields[0][1:] if len(fields[0]) > 1 else ""
            if symbol in self.bots:
                await self.bots[symbol].process_ws_data(tr_id, tr_data)
        
        elif tr_id == "H0GSCNI0":  # 실시간 체결통보
            # 체결통보의 경우 모든 봇에게 전달하거나 종목코드로 필터링
            # 필드 [11]이 종목코드인 경우가 많음
            symbol = fields[11] if len(fields) > 11 else ""
            if symbol in self.bots:
                await self.bots[symbol].process_ws_data(tr_id, tr_data)
            else:
                # 모든 봇에게 전달 (예비책)
                for bot in self.bots.values():
                    await bot.process_ws_data(tr_id, tr_data)

    async def run(self):
        self._running = True
        
        while self._running:
            # 장 시작 대기
            await wait_for_market_open()
            
            logger.info("=== 장 시작: 다중 종목 관리 루프 가동 ===")
            # KIS Access Token 발급 (1분 제한 대응)
            while True:
                res = self.api.issue_access_token()
                if self.api.access_token:
                    logger.info("[Manager] 토큰 발급 및 동기화 완료.")
                    break
                
                # 에러 메시지 확인 (1분 제한 등)
                error_msg = str(res.get("error", ""))
                if "EGW00133" in error_msg or res.get("status_code") == 403:
                    logger.warning("[Manager] 토큰 발급 제한(1분 1회) 감지. 65초 후 재시도합니다...")
                    await asyncio.sleep(65)
                else:
                    logger.error(f"[Manager] 토큰 발급 실패: {error_msg}. 10초 후 재시도...")
                    await asyncio.sleep(10)
            
            self.api.connect_ws()
            
            # Notion 초기화 (DB 연결 등)
            await self.notion.initialize()
            
            # 초기 구독 리스트 (체결통보)
            initial_reqs = [self.api.get_ccnl_notice_req(self.hts_id)]
            
            # 웹소켓 리스너 시작 (백그라운드)
            ws_task = asyncio.create_task(
                self.api.connect_and_listen_ws(initial_reqs, self._ws_callback, self.ws_queue)
            )
            
            # 상태 요약 출력 태스크 시작
            summary_task = asyncio.create_task(self._summary_loop())
            
            try:
                while is_market_open():
                    # 1. 상위 종목 스캔 (거래량 5, 거래대금 5)
                    top_symbols_data = self.scanner.get_top_symbols(limit=5)
                    top_symbols_only = {sym for excd, sym in top_symbols_data}
                    
                    # 2. 새로운 종목 봇 생성 및 실행
                    for excd, symbol in top_symbols_data:
                        if symbol not in self.bots:
                            logger.info(f"[Manager] 새 종목 탐지: {excd} {symbol} -> 봇 생성")
                            bot = V1SmcBot(
                                api=self.api,
                                symbol=symbol,
                                excd=excd,
                                hts_id=self.hts_id,
                                acnt_no=self.acnt_no,
                                acnt_prdt_cd=self.acnt_prdt,
                                alpaca=self.alpaca
                            )
                            # 콜백 설정
                            bot.on_state_change = self._on_bot_state_change
                            bot.on_trade = self._on_bot_trade
                            
                            await bot.setup()
                            tasks = await bot.start_tasks()
                            
                            self.bots[symbol] = bot
                            self.bot_tasks[symbol] = tasks
                            
                            # 웹소켓 구독 추가
                            await self.ws_queue.put(self.api.get_delayed_ccnl_req(symbol))
                    
                    # 3. 비활성 봇 정리 (포지션이 없고 순위권 밖인 경우)
                    # 단, 사용자의 요구사항에 따라 포지션이 있으면 절대 끄지 않음
                    symbols_to_remove = []
                    for sym, bot in self.bots.items():
                        if sym not in top_symbols_only and bot.get_state() == "IDLE":
                            # 포지션이 없고 순위에서도 밀려난 경우만 정리
                            symbols_to_remove.append(sym)
                    
                    for sym in symbols_to_remove:
                        logger.info(f"[Manager] 종목 제외: {sym} (순위 하락 및 IDLE)")
                        for t in self.bot_tasks[sym]:
                            t.cancel()
                        del self.bots[sym]
                        del self.bot_tasks[sym]
                        # 구독 해제는 KIS 정책상 필수는 아니나 필요시 추가 가능
                    
                    await asyncio.sleep(600) # 10분마다 재스캔
                    self._consecutive_errors = 0 # 정상 루프 완료 시 에러 카운트 초기화
            
            except Exception as e:
                self._consecutive_errors += 1
                # 지수 백오프: 1분, 2분, 4분, 8분... (최대 10분)
                wait_min = min(2**(self._consecutive_errors - 1), 10)
                logger.error(f"[Manager] 루프 중 오류 (연속 {self._consecutive_errors}회): {e}", exc_info=True)
                logger.info(f"[Manager] {wait_min}분 후 루프를 재시작합니다.")
                await asyncio.sleep(wait_min * 60)
            finally:
                ws_task.cancel()
                summary_task.cancel()
                for tasks in self.bot_tasks.values():
                    for t in tasks:
                        t.cancel()
                logger.info("=== 장 종료 또는 중단: 관리 루프 정지 ===")

    async def _on_bot_state_change(self, symbol: str, state: str):
        """봇 상태 변경 시 호출되는 콜백"""
        logger.info(f"[Callback] {symbol} 상태 변경: {state}")
        # Notion 실시간 상태 업데이트
        bot = self.bots.get(symbol)
        pos_qty = bot._pos.remaining_qty if bot else 0
        entry_price = bot._pos.entry_price if bot else 0.0
        await self.notion.update_symbol_status(symbol, state, pos_qty, entry_price)
        
        # 디스코드 상태 알림 (주요 상태 변화 시)
        if state in ["STANDBY", "IN_POSITION", "COOLDOWN", "SHUTDOWN"]:
            await self.discord.send_status_alert(
                f"{symbol} 상태 변경", 
                f"현재 상태: **{state}**"
            )

    async def _on_bot_trade(self, symbol: str, trade_type: str, details: dict):
        """봇 거래 발생 시 호출되는 콜백"""
        logger.info(f"[Callback] {symbol} 거래 발생: {trade_type} | {details}")
        # Notion 거래 로그 추가 및 상태 갱신
        await self.notion.add_trade_log(symbol, trade_type, details)
        
        # 디스코드 알림
        await self.discord.send_trade_alert(symbol, trade_type, details)
        
        bot = self.bots.get(symbol)
        if bot:
            await self.notion.update_symbol_status(
                symbol, bot.get_state(), bot._pos.remaining_qty, bot._pos.entry_price
            )

    async def _summary_loop(self):
        """주기적으로 봇들의 상태를 요약하여 출력합니다."""
        while self._running:
            await asyncio.sleep(600) # 10분마다 출력
            if not self.bots:
                logger.info("[Manager Status] 현재 감시 중인 종목이 없습니다.")
                continue
            
            logger.info("=" * 50)
            logger.info(f"[Manager Status] 현재 관리 중인 종목: {len(self.bots)}개")
            for sym, bot in self.bots.items():
                state = bot.get_state()
                pos_qty = bot._pos.remaining_qty
                entry_price = bot._pos.entry_price
                pos_status = f"포지션: {pos_qty}주" if pos_qty > 0 else "IDLE"
                logger.info(f" - {sym:8} | 상태: {state:12} | {pos_status}")
                
                # Notion 대시보드 강제 갱신
                await self.notion.update_symbol_status(sym, state, pos_qty, entry_price)
            logger.info("=" * 50)

    async def shutdown(self):
        self._running = False
        for bot in self.bots.values():
            await bot.shutdown()

async def main():
    load_dotenv(find_dotenv())
    with open("config.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )
    
    app_key = os.getenv("KIS_APP_KEY", "")
    app_secret = os.getenv("KIS_APP_SECRET", "")
    hts_id = os.getenv("KIS_HTS_ID", "")
    acnt_no = os.getenv("KIS_ACCOUNT_NO", "")
    acnt_prdt = os.getenv("KIS_ACCOUNT_PRODUCT_CODE", "01")
    env_dv = os.getenv("KIS_ENV", "demo")

    api = KISApiHandler(appkey=app_key, appsecret=app_secret, env_dv=env_dv)
    manager = SymbolManager(api, config, hts_id, acnt_no, acnt_prdt)

    try:
        await manager.run()
    except KeyboardInterrupt:
        await manager.shutdown()

if __name__ == "__main__":
    asyncio.run(main())