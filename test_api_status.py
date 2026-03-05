import requests
import json

def test_status():
    print("Testing /api/status...")
    try:
        response = requests.get('http://localhost:8002/api/status', headers={'x-api-key': 'tugou_secret_key'})
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            print("Response:", json.dumps(response.json(), indent=2))
        else:
            print("Error:", response.text)
    except Exception as e:
        print(f"Exception: {e}")

if __name__ == "__main__":
    test_status()
