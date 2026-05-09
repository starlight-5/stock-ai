# -*- coding: utf-8 -*-
"""
V1 SMC 다중 종목 자동매매 시스템 매니저
국내(KR) 및 해외(US) 주식 시장을 모두 지원합니다.
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

try:
    import xgboost as xgb
    AI_AVAILABLE = True
except ImportError:
    AI_AVAILABLE = False

logger = logging.getLogger(__name__)

class SymbolManager:
    def __init__(self, api: KISApiHandler, config: dict, hts_id: str, acnt_no: str, acnt_prdt: str):
        self.api = api
        self.config = config
        self.hts_id = hts_id
        self.acnt_no = acnt_no
        self.acnt_prdt = acnt_prdt
        
        # 시장 타입 설정 (기본값 US)
        self.market_type = config.get("trading", {}).get("market_type", "US")
        
        self.scanner = RankScanner(api)
        self.bots: Dict[str, V1SmcBot] = {}
        self.bot_tasks: Dict[str, List[asyncio.Task]] = {}
        
        # AI 모델 로드
        self.ai_model = None
        if AI_AVAILABLE:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            model_path = os.path.join(base_dir, "ai", "smc_ai_filter.json")
            if os.path.exists(model_path):
                try:
                    self.ai_model = xgb.Booster()
                    self.ai_model.load_model(model_path)
                    logger.info(f"[Manager] AI 모델 로드 완료: {model_path}")
                except Exception as e:
                    logger.error(f"[Manager] AI 모델 로드 실패: {e}")
        
        # 알림 로거 설정
        self.notion = NotionLogger(os.getenv("NOTION_TOKEN", ""), os.getenv("NOTION_PAGE_ID", ""))
        self.discord = DiscordLogger(os.getenv("DISCORD_WEBHOOK_URL", ""))
        
        self.ws_queue = asyncio.Queue()
        self._running = False
        self._consecutive_errors = 0

    async def _ws_callback(self, raw_data: str):
        """중앙 웹소켓 콜백: 국내/해외 구분하여 데이터를 배분합니다."""
        if not raw_data or raw_data.startswith("{"): return

        parts = raw_data.split("|")
        if len(parts) < 4: return

        tr_id = parts[1]
        tr_data = parts[3]

        # 1. 해외주식 처리
        if tr_id == "HDFSCNT0":  # 실시간 체결가 (해외)
            fields = tr_data.split("^")
            symbol = fields[0][1:] if len(fields[0]) > 1 else ""
            if symbol in self.bots:
                await self.bots[symbol].process_ws_data(tr_id, tr_data)
        elif tr_id == "H0GSCNI0":  # 실시간 체결통보 (해외)
            fields = tr_data.split("^")
            symbol = fields[11] if len(fields) > 11 else ""
            if symbol in self.bots:
                await self.bots[symbol].process_ws_data(tr_id, tr_data)
            else:
                for bot in self.bots.values(): await bot.process_ws_data(tr_id, tr_data)

        # 2. 국내주식 처리
        elif tr_id == "H0STCNT0":  # 실시간 체결가 (국내)
            # 국내 데이터는 |로 구분된 4번째 파트가 실제 필드들이며, 종목코드는 콜백 호출 전 parts[2]에 위치할 수 있음
            # KISWebSocketHandler에서 이미 raw_data를 넘겨주므로, parts[3]을 다시 분석
            # 국내 체결가는 parts[3] 내부에 필드들이 있음. 종목코드는 헤더(parts[2])에 포함됨.
            symbol = parts[2]
            if symbol in self.bots:
                await self.bots[symbol].process_ws_data(tr_id, tr_data)
        elif tr_id in ["H0STCNI0", "H0STCNI9"]:  # 실시간 체결통보 (국내)
            # 체결통보의 경우 내부 필드를 파싱하여 종목코드 추출
            fields = tr_data.split("|")
            symbol = fields[9] if len(fields) > 9 else "" # 국내 체결통보 종목코드 인덱스 확인 필요
            if symbol in self.bots:
                await self.bots[symbol].process_ws_data(tr_id, tr_data)
            else:
                for bot in self.bots.values(): await bot.process_ws_data(tr_id, tr_data)

    async def run(self):
        self._running = True
        while self._running:
            await wait_for_market_open(self.market_type)
            
            logger.info(f"=== [{self.market_type}] 장 시작: 관리 루프 가동 ===")
            while True:
                res = self.api.issue_access_token()
                if self.api.access_token: break
                await asyncio.sleep(10)
            
            self.api.connect_ws()
            await self.notion.initialize()
            
            # 초기 구독 (체결통보)
            if self.market_type == "KR":
                initial_reqs = [self.api.get_domestic_ccnl_notice_req(self.hts_id)]
            else:
                initial_reqs = [self.api.get_ccnl_notice_req(self.hts_id)]
            
            ws_task = asyncio.create_task(self.api.connect_and_listen_ws(initial_reqs, self._ws_callback, self.ws_queue))
            summary_task = asyncio.create_task(self._summary_loop())
            
            try:
                while is_market_open(self.market_type):
                    # 1. 상위 종목 스캔
                    top_symbols_data = self.scanner.get_top_symbols(market_type=self.market_type, limit=5)
                    top_symbols_only = {sym for excd, sym in top_symbols_data}
                    
                    # 2. 새로운 종목 봇 생성
                    for excd, symbol in top_symbols_data:
                        if symbol not in self.bots:
                            logger.info(f"[Manager] 새 종목 탐지: {symbol} ({excd})")
                            bot = V1SmcBot(
                                api=self.api, symbol=symbol, excd=excd, 
                                market_type=self.market_type, ai_model=self.ai_model
                            )
                            bot.acnt_no = self.acnt_no
                            bot.acnt_prdt_cd = self.acnt_prdt
                            bot.on_state_change = self._on_bot_state_change
                            bot.on_trade = self._on_bot_trade
                            
                            await bot.setup()
                            self.bots[symbol] = bot
                            
                            # 웹소켓 구독 추가
                            if self.market_type == "KR":
                                await self.ws_queue.put(self.api.get_domestic_price_req(symbol))
                            else:
                                await self.ws_queue.put(self.api.get_delayed_ccnl_req(symbol))
                    
                    # 3. 비활성 봇 정리
                    to_remove = [s for s in self.bots if s not in top_symbols_only and self.bots[s]._state == "MONITORING"]
                    for s in to_remove:
                        logger.info(f"[Manager] 종목 제거: {s}")
                        del self.bots[s]
                    
                    await asyncio.sleep(300) # 5분마다 스캔
            except Exception as e:
                logger.error(f"[Manager] 루프 에러: {e}", exc_info=True)
                await asyncio.sleep(60)
            finally:
                ws_task.cancel()
                summary_task.cancel()

    async def _on_bot_state_change(self, symbol: str, state: str):
        logger.info(f"[Callback] {symbol} 상태: {state}")
        await self.notion.update_symbol_status(symbol, state, 0, 0.0) # 실제 구현 시 수량/평단가 연동
        if state in ["IN_POSITION", "SHUTDOWN"]:
            await self.discord.send_status_alert(symbol, f"상태 변경: {state}")

    async def _on_bot_trade(self, symbol: str, trade_type: str, details: dict):
        logger.info(f"[Callback] {symbol} 거래: {trade_type}")
        await self.notion.add_trade_log(symbol, trade_type, details)
        await self.discord.send_trade_alert(symbol, trade_type, details)

    async def _summary_loop(self):
        while self._running:
            await asyncio.sleep(600)
            if self.bots:
                logger.info("=" * 30)
                for s, b in self.bots.items():
                    logger.info(f" - {s:8} | 상태: {b._state.value}")
                logger.info("=" * 30)

    async def shutdown(self):
        self._running = False
        await self.notion.close()

async def main():
    load_dotenv(find_dotenv())
    with open("config.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
    
    api = KISApiHandler(os.getenv("KIS_APP_KEY"), os.getenv("KIS_APP_SECRET"), os.getenv("KIS_ENV", "demo"))
    manager = SymbolManager(api, config, os.getenv("KIS_HTS_ID"), os.getenv("KIS_ACCOUNT_NO"), os.getenv("KIS_ACCOUNT_PRODUCT_CODE", "01"))
    try:
        await manager.run()
    except:
        await manager.shutdown()

if __name__ == "__main__":
    asyncio.run(main())