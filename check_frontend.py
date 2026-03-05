import requests

def check_frontend():
    url = "http://localhost:3000"
    print(f"Checking frontend at {url}...")
    try:
        r = requests.get(url, timeout=5)
        print(f"  Status: {r.status_code}")
        if r.status_code == 200:
            print(f"  Frontend is WORKING.")
        else:
            print(f"  Frontend returned error: {r.status_code}")
    except Exception as e:
        print(f"  Frontend check FAILED: {e}")

if __name__ == "__main__":
    check_frontend()
