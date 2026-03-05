import requests
import json

BASE_URL = "http://localhost:8001/api"
HEADERS = {"X-API-Key": "tugou_secret_key"}

def check_endpoint(endpoint):
    try:
        url = f"{BASE_URL}/{endpoint}"
        print(f"Checking {url}...")
        response = requests.get(url, headers=HEADERS, timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"✅ {endpoint}: Success")
            print(json.dumps(data, indent=2, ensure_ascii=False)[:500])
        else:
            print(f"❌ {endpoint}: Failed with {response.status_code}")
            print(response.text)
    except Exception as e:
        print(f"❌ {endpoint}: Error {str(e)}")

if __name__ == "__main__":
    check_endpoint("simulation/stats")
