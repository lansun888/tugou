import requests
import json
import time

def test_endpoint(endpoint):
    print(f"Testing {endpoint}...")
    try:
        response = requests.get(f'http://localhost:8002{endpoint}', headers={'x-api-key': 'tugou_secret_key'})
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"Success! Got {len(data)} items.")
            if len(data) > 0:
                print(json.dumps(data[:1], indent=2))
        else:
            print(f"Error Response: {response.text}")
    except Exception as e:
        print(f"Exception: {e}")

if __name__ == "__main__":
    test_endpoint('/api/trades?page=1&limit=5')
    test_endpoint('/api/stats/daily')
    test_endpoint('/api/stats/hourly')
