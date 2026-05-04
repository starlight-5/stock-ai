# -*- coding: utf-8 -*-
"""
자동매매 봇 진입점(Entry Point) 입니다.
config.yaml 에서 설정을 읽어 지정된 전략 봇을 실행합니다.

실행 방법:
  python -m my_trading_bot.main

종료:
  Ctrl+C
"""

import asyncio
import logging
import os
import sys

import yaml
from dotenv import load_dotenv, find_dotenv

# 프로젝트 루트를 Python 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from my_trading_bot.core.api_handler import KISApiHandler
from my_trading_bot.strategies.v1_smc.logic import V1SmcBot


def load_config(path: str = None) -> dict:
    """config.yaml 파일을 읽어 딕셔너리로 반환합니다."""
    if path is None:
        path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logging(level: str = "INFO") -> None:
    """로깅 설정을 초기화합니다."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


async def main() -> None:
    """메인 비동기 진입점입니다."""
    # 환경변수 및 설정 로드
    load_dotenv(find_dotenv())
    config = load_config()

    setup_logging(config.get("env", {}).get("log_level", "INFO"))
    logger = logging.getLogger(__name__)

    # KIS API 자격증명 일괄 로드 (.env 우선, config.yaml 기본값 활용)
    app_key    = os.getenv("KIS_APP_KEY", "")
    app_secret = os.getenv("KIS_APP_SECRET", "")
    hts_id     = os.getenv("KIS_HTS_ID", config.get("trading", {}).get("hts_id", ""))
    acnt_no    = os.getenv("KIS_ACCOUNT_NO", "")
    acnt_prdt  = os.getenv("KIS_ACCOUNT_PRODUCT_CODE", "01")
    # 운영 환경: .env 의 KIS_ENV 가 config.yaml 보다 우선
    env_dv     = os.getenv("KIS_ENV", config.get("env", {}).get("mode", "demo"))

    if not app_key or not app_secret:
        logger.error("KIS_APP_KEY 또는 KIS_APP_SECRET 환경변수가 설정되지 않았습니다.")
        sys.exit(1)

    # API 핸들러 초기화
    api = KISApiHandler(appkey=app_key, appsecret=app_secret, env_dv=env_dv)

    # 전략 선택
    strategy_name = config.get("strategy", "v1_smc")
    logger.info(f"전략: {strategy_name} | 환경: {env_dv}")

    if strategy_name == "v1_smc":
        trading_cfg = config.get("trading", {})
        bot = V1SmcBot(
            api          = api,
            symbol       = trading_cfg.get("symbol", "AAPL"),
            excd         = trading_cfg.get("excd", "NAS"),
            hts_id       = hts_id,
            acnt_no      = acnt_no,
            acnt_prdt_cd = acnt_prdt,
        )
    else:
        logger.error(f"알 수 없는 전략: {strategy_name}")
        sys.exit(1)

    logger.info("=== 봇 시작 ===")
    try:
        await bot.run()
    except KeyboardInterrupt:
        logger.info("사용자 중단 요청 (Ctrl+C)")
        await bot.shutdown()
    except Exception as e:
        logger.critical(f"예기치 않은 오류: {e}", exc_info=True)
        await bot.shutdown()
    finally:
        logger.info("=== 봇 종료 ===")


if __name__ == "__main__":
    asyncio.run(main())