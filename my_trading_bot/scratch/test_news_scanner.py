# -*- coding: utf-8 -*-
"""
NewsScanner 모듈 동작 테스트 스크립트
"""
import asyncio
import os
import sys
import logging
import yaml
from dotenv import load_dotenv, find_dotenv

# 프로젝트 루트 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from my_trading_bot.core.api_handler import KISApiHandler
from my_trading_bot.core.news_scanner import NewsScanner
from my_trading_bot.strategies.v1_smc.params import WATCHLIST


async def test_scanner():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    logger = logging.getLogger(__name__)

    # 1. 환경 변수 로드
    load_dotenv(find_dotenv())
    app_key = os.getenv("KIS_APP_KEY")
    app_secret = os.getenv("KIS_APP_SECRET")
    env = os.getenv("KIS_ENV", "demo")

    if not app_key or not app_secret:
        logger.error("❌ KIS_APP_KEY 또는 KIS_APP_SECRET이 설정되지 않았습니다.")
        return

    # 2. API 핸들러 초기화
    api = KISApiHandler(app_key, app_secret, env)
    api.issue_access_token()
    
    if not api.access_token:
        logger.error("❌ API 토큰 발급 실패")
        return

    # 3. NewsScanner 실행
    logger.info("🚀 NewsScanner 테스트 시작...")
    scanner = NewsScanner(api, top_n=5)
    
    # 4. 스캔 수행
    selected_stocks = scanner.scan(WATCHLIST)
    
    logger.info("=" * 50)
    logger.info(f"✅ 테스트 완료! 최종 선별된 종목 ({len(selected_stocks)}개):")
    for excd, sym in selected_stocks:
        logger.info(f"  - {excd}:{sym}")
    logger.info("=" * 50)

if __name__ == "__main__":
    asyncio.run(test_scanner())
