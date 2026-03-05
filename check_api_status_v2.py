import requests
import json
import sys

BASE_URL = "http://localhost:8001/api"
HEADERS = {"X-API-Key": "tugou_secret_key"}

def check_endpoint(endpoint):
    try:
        url = f"{BASE_URL}/{endpoint}"
        print(f"Checking {url}...")
        response = requests.get(url, headers=HEADERS, timeout=20)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list):
                print(f"✅ {endpoint}: Success, {len(data)} items")
                if len(data) > 0:
                    print(f"   Sample: {json.dumps(data[0], ensure_ascii=False)[:100]}...")
            elif isinstance(data, dict):
                # For logs, it returns {"logs": [...]}
                if "logs" in data:
                     print(f"✅ {endpoint}: Success, {len(data['logs'])} logs")
                     if len(data['logs']) > 0:
                         print(f"   Sample: {json.dumps(data['logs'][-1], ensure_ascii=False)[:100]}...")
                else:
                    print(f"✅ {endpoint}: Success, response: {json.dumps(data, ensure_ascii=False)[:100]}...")
            else:
                 print(f"✅ {endpoint}: Success, type: {type(data)}")
        else:
            print(f"❌ {endpoint}: Failed with {response.status_code}")
            print(response.text)
    except Exception as e:
        print(f"❌ {endpoint}: Error {str(e)}")

if __name__ == "__main__":
    check_endpoint("logs")
    check_endpoint("discoveries")
    check_endpoint("positions")
    check_endpoint("status")
