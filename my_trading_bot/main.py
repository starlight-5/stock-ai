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
        
        # 웹소켓 추가 구독을 위한 큐
        self.ws_queue = asyncio.Queue()
        self._running = False

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
            self.api.issue_access_token()
            self.api.connect_ws()
            
            # 초기 구독 리스트 (체결통보)
            initial_reqs = [self.api.get_ccnl_notice_req(self.hts_id)]
            
            # 웹소켓 리스너 시작 (백그라운드)
            ws_task = asyncio.create_task(
                self.api.connect_and_listen_ws(initial_reqs, self._ws_callback, self.ws_queue)
            )
            
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
                                acnt_prdt_cd=self.acnt_prdt
                            )
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
            
            except Exception as e:
                logger.error(f"[Manager] 루프 중 오류: {e}", exc_info=True)
            finally:
                ws_task.cancel()
                for tasks in self.bot_tasks.values():
                    for t in tasks:
                        t.cancel()
                logger.info("=== 장 종료 또는 중단: 관리 루프 정지 ===")

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