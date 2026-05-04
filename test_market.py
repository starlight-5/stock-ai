import os
import sys

# 프로젝트 루트를 경로에 추가
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from my_trading_bot.core.api_handler import KISApiHandler

def test_market_methods():
    print("Testing KISApiHandler Market Methods...")
    handler = KISApiHandler("dummy_key", "dummy_secret", "demo")
    
    # Check if methods exist and can be called (we don't mock the requests here, just checking binding)
    # We will just print the method references to ensure they exist.
    methods = [
        handler.get_price_detail,
        handler.get_asking_price,
        handler.get_price,
        handler.get_quot_ccnl,
        handler.get_time_itemchartprice,
        handler.get_dailyprice,
        handler.get_daily_chartprice,
        handler.get_inquire_search,
        handler.get_countries_holiday,
        handler.get_search_info,
        handler.get_industry_theme,
        handler.get_industry_price
    ]
    
    for m in methods:
        print(f"Found method: {m.__name__}")

    print("All market methods are properly exposed!")

if __name__ == "__main__":
    test_market_methods()
