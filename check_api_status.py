
import requests
import sys

BASE_URL = "http://localhost:8001/api"

def check_endpoint(name, url):
    try:
        response = requests.get(url)
        print(f"Checking {name} ({url})... Status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            # print(f"Data: {data}")
            if isinstance(data, list):
                print(f"Received {len(data)} items")
            elif isinstance(data, dict):
                print(f"Received data keys: {list(data.keys())}")
                if 'data' in data:
                     if isinstance(data['data'], list):
                        print(f"Inner data count: {len(data['data'])}")
                     else:
                        print(f"Inner data keys: {list(data['data'].keys())}")
            return True
        else:
            print(f"Error: {response.text}")
            return False
    except Exception as e:
        print(f"Failed to connect to {url}: {e}")
        return False

print("Starting API checks...")
check_endpoint("Dashboard Stats", f"{BASE_URL}/dashboard/stats")
check_endpoint("Positions", f"{BASE_URL}/positions")
check_endpoint("System Logs", f"{BASE_URL}/system/logs")
check_endpoint("Discovered Pairs", f"{BASE_URL}/pairs/discovered")
