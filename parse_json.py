import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    d = json.load(f)
    with open("mapped_apis.txt", "w", encoding="utf-8") as out:
        for r in d['results']:
            out.write(f"{r['api_name']} -> {r['function_name']}\n")
