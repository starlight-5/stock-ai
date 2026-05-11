import os
import logging
from my_trading_bot.core.api_handler import KISApiHandler
from dotenv import load_dotenv, find_dotenv

logging.basicConfig(level=logging.INFO)
load_dotenv(find_dotenv())

def test_token():
    appkey = os.getenv("KIS_APP_KEY")
    appsecret = os.getenv("KIS_APP_SECRET")
    env_dv = os.getenv("KIS_ENV", "real")
    
    print(f"Testing with Key: {appkey[:5]}***, Secret: {appsecret[:5]}***, Env: {env_dv}")
    
    api = KISApiHandler(appkey, appsecret, env_dv)
    res = api.issue_access_token()
    
    print(f"Response: {res}")
    print(f"API Access Token: {api.access_token[:10]}...")

if __name__ == "__main__":
    test_token()
