import json
import re

def extract_info(filepath):
    try:
        with open(filepath, encoding='utf-8') as f:
            data = json.load(f)
            content = data['results']['main']['content']
            
            tr_id_match = re.search(r'tr_id\s*=\s*["\']([^"\']+)["\']|TR_ID\s*=\s*["\']([^"\']+)["\']', content, re.IGNORECASE)
            tr_id = tr_id_match.group(1) or tr_id_match.group(2) if tr_id_match else "UNKNOWN"
            
            url_match = re.search(r'url\s*=\s*.*?["\']([^"\']+)["\']|PATH\s*=\s*["\']([^"\']+)["\']', content, re.IGNORECASE)
            url = url_match.group(1) or url_match.group(2) if url_match else "UNKNOWN"
            
            print(f"File: {filepath.split('/')[-2]}")
            print(f"URL: {url}")
            print(f"TR_ID: {tr_id}")
            print("-" * 40)
    except Exception as e:
        print(f"Error parsing {filepath}: {e}")

files = [
    "C:/Users/apep1/.gemini/antigravity/brain/e2350713-e50a-4aa6-9ff0-84606dea982f/.system_generated/steps/354/output.txt",
    "C:/Users/apep1/.gemini/antigravity/brain/e2350713-e50a-4aa6-9ff0-84606dea982f/.system_generated/steps/355/output.txt",
    "C:/Users/apep1/.gemini/antigravity/brain/e2350713-e50a-4aa6-9ff0-84606dea982f/.system_generated/steps/356/output.txt",
    "C:/Users/apep1/.gemini/antigravity/brain/e2350713-e50a-4aa6-9ff0-84606dea982f/.system_generated/steps/357/output.txt"
]

for f in files:
    extract_info(f)
