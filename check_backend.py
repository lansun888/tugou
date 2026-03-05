import requests

def check_backend():
    url = "http://127.0.0.1:8002/api/status"
    print(f"Checking backend at {url}...")
    try:
        r = requests.get(url, headers={'x-api-key': 'tugou_secret_key'}, timeout=5)
        print(f"  Status: {r.status_code}")
        if r.status_code == 200:
            print(f"  Backend is WORKING: {r.json()}")
        else:
            print(f"  Backend returned error: {r.status_code}")
            print(f"  Response: {r.text}")
    except Exception as e:
        print(f"  Backend check FAILED: {e}")

if __name__ == "__main__":
    check_backend()
