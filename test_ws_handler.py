import os
from dotenv import load_dotenv
import asyncio
from my_trading_bot.core.api_handler import KISApiHandler

def test_websocket_handlers():
    load_dotenv()
    APP_KEY = os.getenv("KIS_APP_KEY", "")
    APP_SECRET = os.getenv("KIS_APP_SECRET", "")
    ENV_DV = os.getenv("BASE_URL_REAL", "real")  # Just as mock config
    
    # We use a dummy env_dv for test, like "demo"
    api = KISApiHandler(appkey=APP_KEY, appsecret=APP_SECRET, env_dv="demo")
    
    print("=== 해외주식 웹소켓 구독 요청 페이로드 테스트 ===")
    
    # 1. 해외주식 실시간호가 (미국)
    asking_price_req = api.get_asking_price_req("AAPL")
    print(f"[실시간호가] {asking_price_req}")
    
    # 2. 해외주식 실시간지연체결가
    delayed_ccnl_req = api.get_delayed_ccnl_req("TSLA")
    print(f"[실시간지연체결가] {delayed_ccnl_req}")
    
    # 3. 해외주식 실시간체결통보
    ccnl_notice_req = api.get_ccnl_notice_req("MY_HTS_ID")
    print(f"[실시간체결통보] {ccnl_notice_req}")
    
    print("테스트 완료")

if __name__ == "__main__":
    test_websocket_handlers()
