# -*- coding: utf-8 -*-
"""
미국 주식 정규장 시간(Market Hours)을 감지하고 대기하는 유틸리티 모듈입니다.

[미국 정규장 시간]
  뉴욕 거래소(NYSE) / 나스닥(NASDAQ)
  - 운영 시간: 월~금 09:30 ~ 16:00 (미국 동부시간, ET)
  - ET = EST(UTC-5) / EDT(UTC-4) — pytz가 자동으로 DST(서머타임)를 처리합니다.
  - 한국시간(KST) 기준:
    - 서머타임 적용 시(3월~11월): 22:30 ~ 05:00 (다음날)
    - 서머타임 미적용 시(11월~3월): 23:30 ~ 06:00 (다음날)

[공휴일 처리]
  미국 증시 공휴일은 KIS API의 get_countries_holiday()를 통해 확인할 수 있으나,
  현재는 기본 주말 + 시간 체크만 수행합니다.
  공휴일에 봇이 실행될 경우, 장 오픈 시간을 계속 기다리다가
  당일 장 마감 시간(16:00 ET) 이후에 다음날로 넘어갑니다.
"""

import asyncio
import logging
from datetime import datetime, time, timedelta
from typing import Tuple

import pytz

logger = logging.getLogger(__name__)

# 미국 동부 시간대 (EST/EDT 자동 처리)
ET = pytz.timezone("America/New_York")

# 정규장 시작/종료 시각 (ET 기준)
MARKET_OPEN  = time(9, 30, 0)   # 09:30 ET
MARKET_CLOSE = time(16, 0, 0)   # 16:00 ET


def now_et() -> datetime:
    """현재 시각을 미국 동부시간(ET)으로 반환합니다."""
    return datetime.now(ET)


def is_market_open() -> bool:
    """
    현재 미국 정규장이 열려 있는지 확인합니다.
    
    :return: True이면 현재 정규장 시간, False이면 장 외 시간
    """
    now = now_et()
    
    # 주말(토=5, 일=6) 체크
    if now.weekday() >= 5:
        return False
    
    current_time = now.time()
    return MARKET_OPEN <= current_time < MARKET_CLOSE


def get_seconds_until_open() -> float:
    """
    다음 정규장 오픈까지 남은 시간(초)을 반환합니다.
    
    :return: 장 오픈까지 남은 초 수 (이미 열려 있으면 0)
    """
    if is_market_open():
        return 0.0

    now = now_et()
    today = now.date()

    # 오늘 장 오픈 시각 계산
    open_today = ET.localize(datetime.combine(today, MARKET_OPEN))

    # 현재가 장 오픈 전이고 평일이면 → 오늘 오픈까지 대기
    if now < open_today and now.weekday() < 5:
        return (open_today - now).total_seconds()

    # 그 외(장 마감 후, 주말) → 다음 평일 오픈까지 대기
    next_open = open_today + timedelta(days=1)
    while next_open.weekday() >= 5:  # 주말이면 건너뜀
        next_open += timedelta(days=1)

    return (next_open - now).total_seconds()


def format_wait_time(seconds: float) -> str:
    """대기 시간을 'X시간 Y분 Z초' 형식의 문자열로 반환합니다."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h}시간 {m}분 {s}초"


async def wait_for_market_open(check_interval_sec: int = 60) -> None:
    """
    미국 정규장이 열릴 때까지 비동기로 대기합니다.
    1분마다 남은 시간을 로그에 출력합니다.
    
    :param check_interval_sec: 상태 로그 출력 주기 (초, 기본 60초)
    """
    if is_market_open():
        logger.info("미국 정규장이 이미 열려 있습니다. 즉시 거래를 시작합니다.")
        return

    wait_sec = get_seconds_until_open()
    now_kst = datetime.now(pytz.timezone("Asia/Seoul"))
    open_et = now_et() + timedelta(seconds=wait_sec)

    logger.info(
        f"미국 정규장 대기 중...\n"
        f"  현재 한국시간: {now_kst.strftime('%Y-%m-%d %H:%M:%S KST')}\n"
        f"  장 오픈 예정 (ET): {open_et.strftime('%Y-%m-%d %H:%M:%S ET')}\n"
        f"  대기 시간: {format_wait_time(wait_sec)}"
    )

    while True:
        if is_market_open():
            logger.info("미국 정규장 오픈! 거래를 시작합니다.")
            return

        wait_sec = get_seconds_until_open()
        
        # 남은 시간이 check_interval보다 짧으면 딱 맞게 대기
        sleep_sec = min(check_interval_sec, wait_sec)
        if sleep_sec <= 0:
            await asyncio.sleep(1)
            continue

        logger.info(f"  장 오픈까지 남은 시간: {format_wait_time(wait_sec)}")
        await asyncio.sleep(sleep_sec)


def is_market_closing_soon(minutes: int = 30) -> bool:
    """
    정규장 마감까지 지정한 시간(분) 이하로 남았는지 확인합니다.
    신규 진입을 막을 때 활용합니다.
    
    :param minutes: 기준 분 수 (기본 30분)
    :return: 마감 N분 이내이면 True
    """
    now = now_et()
    if not is_market_open():
        return False
    close_today = ET.localize(datetime.combine(now.date(), MARKET_CLOSE))
    remaining = (close_today - now).total_seconds()
    return remaining <= minutes * 60
