import json

with open("C:/Users/apep1/.gemini/antigravity/brain/e2350713-e50a-4aa6-9ff0-84606dea982f/.system_generated/steps/286/output.txt", encoding="utf-8") as f:
    d = json.load(f)
    for r in d['results']:
        print(f"{r['api_name']} -> {r['function_name']}")
