import os
import sys

# 프로젝트 루트를 경로에 추가
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from my_trading_bot.core.api_handler import KISApiHandler

def test_analysis_methods():
    print("Testing KISApiHandler Analysis Methods...")
    handler = KISApiHandler("dummy_key", "dummy_secret", "demo")
    
    methods = [
        handler.get_price_fluct,
        handler.get_volume_surge,
        handler.get_volume_power,
        handler.get_updown_rate,
        handler.get_trade_vol,
        handler.get_trade_pbmn,
        handler.get_trade_growth,
        handler.get_trade_turnover
    ]
    
    for m in methods:
        print(f"Found method: {m.__name__}")

    print("All analysis methods are properly exposed!")

if __name__ == "__main__":
    test_analysis_methods()
