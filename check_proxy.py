import requests
import time

def check_proxy():
    url = "http://localhost:3000/api/status"
    print(f"Checking proxy at {url}...")
    try:
        r = requests.get(url, headers={'x-api-key': 'tugou_secret_key'}, timeout=5)
        print(f"  Status: {r.status_code}")
        if r.status_code == 200:
            print("  Proxy is WORKING.")
            print(f"  Response: {r.json()}")
        else:
            print(f"  Proxy returned error: {r.status_code}")
            print(f"  Response: {r.text}")
    except Exception as e:
        print(f"  Proxy check FAILED: {e}")

if __name__ == "__main__":
    time.sleep(2) # Wait for server to be fully ready
    check_proxy()
