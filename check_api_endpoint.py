import requests
import os
from dotenv import load_dotenv

# Load env to get API Key
load_dotenv()
API_KEY = os.getenv("WEB_API_KEY", "tugou_secret_key")

BASE_URL = "http://localhost:8001/api"
HEADERS = {"X-API-Key": API_KEY}

def check_endpoint(endpoint):
    url = f"{BASE_URL}{endpoint}"
    print(f"Checking {url}...")
    try:
        response = requests.get(url, headers=HEADERS, timeout=5)
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list):
                print(f"Result is a list with {len(data)} items")
                if len(data) > 0:
                    print("Sample item:", data[0])
            elif isinstance(data, dict):
                print("Result is a dict keys:", list(data.keys()))
                if "logs" in data:
                    print(f"Logs count: {len(data['logs'])}")
            else:
                print("Result:", data)
        else:
            print("Error:", response.text)
    except Exception as e:
        print(f"Request failed: {e}")

if __name__ == "__main__":
    check_endpoint("/discoveries?limit=5")
    # Check logs
    check_endpoint("/logs?limit=5")
    
    # Check status
    check_endpoint("/status")
    
    # Check simulation stats
    check_endpoint("/simulation/stats?days=7")
