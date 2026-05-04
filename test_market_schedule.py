import sys
sys.path.insert(0, '.')

from my_trading_bot.utils.market_schedule import (
    is_market_open, get_seconds_until_open,
    format_wait_time, now_et, is_market_closing_soon
)

et_now = now_et()
weekdays = ["월", "화", "수", "목", "금", "토", "일"]
print("=== market_schedule 유틸 테스트 ===")
print(f"현재 ET 시각: {et_now.strftime('%Y-%m-%d %H:%M:%S')} ET")
print(f"요일: {weekdays[et_now.weekday()]}요일")
print(f"정규장 열림 여부: {is_market_open()}")
wait = get_seconds_until_open()
print(f"장 오픈까지: {format_wait_time(wait)}")
print(f"장 마감 30분 이내: {is_market_closing_soon(30)}")
print()
print("=== 테스트 통과! ===")
