import requests
import time
import sys

BASE_URL = "http://localhost:8000"
API_KEY = "tugou_secret_key"
HEADERS = {"X-API-Key": API_KEY}

def test_status():
    print("Testing /api/status...")
    try:
        response = requests.get(f"{BASE_URL}/api/status", headers=HEADERS)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            print("Response:", response.json())
        else:
            print("Error:", response.text)
    except Exception as e:
        print(f"Failed: {e}")

def test_positions():
    print("\nTesting /api/positions...")
    try:
        response = requests.get(f"{BASE_URL}/api/positions", headers=HEADERS)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            print("Positions count:", len(response.json()))
            # print("Response:", response.json())
        else:
            print("Error:", response.text)
    except Exception as e:
        print(f"Failed: {e}")

def test_trades():
    print("\nTesting /api/trades...")
    try:
        response = requests.get(f"{BASE_URL}/api/trades", headers=HEADERS)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            print("Trades count:", len(response.json()))
        else:
            print("Error:", response.text)
    except Exception as e:
        print(f"Failed: {e}")

def test_config():
    print("\nTesting /api/config...")
    try:
        response = requests.get(f"{BASE_URL}/api/config", headers=HEADERS)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            config = response.json()
            print("Config loaded. Keys:", list(config.keys()))
        else:
            print("Error:", response.text)
    except Exception as e:
        print(f"Failed: {e}")

def test_bot_control():
    print("\nTesting /api/bot/pause...")
    try:
        # Pause
        response = requests.post(f"{BASE_URL}/api/bot/pause", headers=HEADERS)
        print(f"Pause Status Code: {response.status_code}")
        print("Pause Response:", response.json())
        
        # Resume
        response = requests.post(f"{BASE_URL}/api/bot/pause", headers=HEADERS)
        print(f"Resume Status Code: {response.status_code}")
        print("Resume Response:", response.json())
    except Exception as e:
        print(f"Failed: {e}")

if __name__ == "__main__":
    # Wait a bit for server to be fully ready if just started
    time.sleep(2)
    
    test_status()
    test_positions()
    test_trades()
    test_config()
    test_bot_control()
