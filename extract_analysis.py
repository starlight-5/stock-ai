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

results = []
for func in funcs:
    url = f"https://raw.githubusercontent.com/koreainvestment/open-trading-api/main/examples_llm/overseas_stock/{func}/{func}.py"
    r = requests.get(url)
    if r.status_code == 200:
        content = r.text
        # extract URL
        match_url = re.search(r'URL\s*=\s*["\']([^"\']+)["\']', content)
        # extract TR_ID
        match_trid = re.search(r'tr_id\s*=\s*["\']([^"\']+)["\']', content, re.IGNORECASE)
        
        path = match_url.group(1) if match_url else "UNKNOWN"
        tr_id = match_trid.group(1) if match_trid else "UNKNOWN"
        
        results.append(f"{func}: URL={path}, TR_ID={tr_id}")
    else:
        results.append(f"{func}: Failed to fetch {url}")

with open("analysis_meta.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(results))
