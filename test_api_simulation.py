import requests
import json

def test_simulation_stats():
    print("Testing /api/simulation/stats?days=7...")
    try:
        response = requests.get('http://localhost:8002/api/simulation/stats?days=7', headers={'x-api-key': 'tugou_secret_key'})
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            print("Response:", json.dumps(response.json(), indent=2))
        else:
            print("Error:", response.text)
    except Exception as e:
        print(f"Exception: {e}")

if __name__ == "__main__":
    test_simulation_stats()
