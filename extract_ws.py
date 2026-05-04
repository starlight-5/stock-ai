import requests
import re

funcs = [
    "asking_price",
    "delayed_ccnl",
    "ccnl_notice"
]

results = []
for func in funcs:
    url = f"https://raw.githubusercontent.com/koreainvestment/open-trading-api/main/examples_llm/overseas_stock/{func}/{func}.py"
    r = requests.get(url)
    if r.status_code == 200:
        content = r.text
        # We want to see how TR_ID and endpoints are defined
        match_trid = re.search(r'tr_id\s*=\s*["\']([^"\']+)["\']', content, re.IGNORECASE)
        tr_id = match_trid.group(1) if match_trid else "UNKNOWN"
        results.append(f"{func}: TR_ID={tr_id}")
    else:
        results.append(f"{func}: Failed to fetch {url}")

with open("ws_meta.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(results))
