import requests
import re

funcs = [
    "price_fluct",
    "volume_surge",
    "volume_power",
    "updown_rate",
    "trade_vol",
    "trade_pbmn",
    "trade_growth",
    "trade_turnover"
]

for func in funcs:
    url = f"https://raw.githubusercontent.com/koreainvestment/open-trading-api/main/examples_llm/overseas_stock/{func}/chk_{func}.py"
    r = requests.get(url)
    if r.status_code == 200:
        content = r.text
        # match params dictionary
        match_params = re.search(r'params\s*=\s*({[^}]+})', content, re.MULTILINE | re.DOTALL)
        if match_params:
            print(f"--- {func} params ---")
            print(match_params.group(1))
    else:
        print(f"Failed to fetch {url}")
