import asyncio
import os
import json
import requests
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

def test_api():
    app_key = os.getenv("KIS_APP_KEY", "")
    app_secret = os.getenv("KIS_APP_SECRET", "")
    env_dv = os.getenv("KIS_ENV", "real")
    
    base_url = "https://openapi.koreainvestment.com:9443" if env_dv == "real" else "https://openapivts.koreainvestment.com:29443"
    
    # 1. Access Token
    auth_url = f"{base_url}/oauth2/tokenP"
    auth_data = {
        "grant_type": "client_credentials",
        "appkey": app_key,
        "appsecret": app_secret
    }
    res = requests.post(auth_url, data=json.dumps(auth_data))
    if res.status_code != 200:
        print(f"Auth failed: {res.status_code} - {res.text}")
        return
    token = res.json()["access_token"]
    
    # 2. Trade Vol
    vol_url = f"{base_url}/uapi/overseas-stock/v1/ranking/trade-vol"
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": app_key,
        "appsecret": app_secret,
        "tr_id": "HHDFS76310010",
        "custtype": "P"
    }
    params = {
        "AUTH": "", "EXCD": "NAS", "NDAY": "0", "VOL_RANG": "0", "PRC1": "", "PRC2": "", "KEYB": ""
    }
    
    res = requests.get(vol_url, headers=headers, params=params)
    data = res.json()
    print(f"RT_CD: {data.get('rt_cd')}, MSG1: {data.get('msg1')}")
    print("Response Keys:", data.keys())
    for k, v in data.items():
        if isinstance(v, list):
            print(f"Key '{k}' is a list of length {len(v)}")
            if v: print(f"  First element keys: {v[0].keys()}")
        elif isinstance(v, dict):
            print(f"Key '{k}' is a dict with keys: {v.keys()}")
        else:
            print(f"Key '{k}' is {type(v)}: {v}")

    # 3. Trade Pbmn
    pbmn_url = f"{base_url}/uapi/overseas-stock/v1/ranking/trade-pbmn"
    headers["tr_id"] = "HHDFS76320010"
    res = requests.get(pbmn_url, headers=headers, params=params)
    data = res.json()
    print("\n--- Trade Pbmn ---")
    print(f"RT_CD: {data.get('rt_cd')}, MSG1: {data.get('msg1')}")
    for k, v in data.items():
        if isinstance(v, list):
            print(f"Key '{k}' is a list of length {len(v)}")
        elif isinstance(v, dict):
            print(f"Key '{k}' is a dict with keys: {v.keys()}")
        else:
            print(f"Key '{k}' is {type(v)}")

if __name__ == "__main__":
    test_api()
