# -*- coding: utf-8 -*-
import os
import json
import asyncio
from dotenv import load_dotenv
from my_trading_bot.core.api_handler import KISApiHandler

async def debug_balance():
    load_dotenv()
    
    api = KISApiHandler(
        appkey=os.getenv("KIS_APP_KEY"),
        appsecret=os.getenv("KIS_APP_SECRET"),
        env_dv=os.getenv("KIS_ENV", "real")
    )
    
    # 토큰 발급
    api.issue_access_token()
    
    acnt_no = os.getenv("KIS_ACCOUNT_NO")
    acnt_prdt_cd = os.getenv("KIS_ACCOUNT_PRODUCT_CODE", "01")
    
    print(f"\n--- Checking Balance for Account: {acnt_no}-{acnt_prdt_cd} ---")
    
    # 1. 해외주식 체결기준현재잔고 (CTRP6504R)
    res1 = api.inquire_overseas_present_balance(acnt_no, acnt_prdt_cd)
    print("\n[1] inquire_overseas_present_balance (CTRP6504R) Raw Response:")
    print(json.dumps(res1, indent=2, ensure_ascii=False))
    
    # 2. 해외주식 잔고 (TTTS3012R) - 대안
    res2 = api.inquire_overseas_balance(acnt_no, acnt_prdt_cd, ovrs_excg_cd="NASD")
    print("\n[2] inquire_overseas_balance (TTTS3012R) Raw Response:")
    print(json.dumps(res2, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    asyncio.run(debug_balance())
