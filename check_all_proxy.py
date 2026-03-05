import requests
import json

def check_all():
    base_url = "http://localhost:3000/api"
    headers = {'x-api-key': 'tugou_secret_key'}
    
    endpoints = ["/status", "/trades", "/positions"]
    
    for ep in endpoints:
        url = base_url + ep
        print(f"Checking {url}...")
        try:
            r = requests.get(url, headers=headers, timeout=5)
            print(f"  Status: {r.status_code}")
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    print(f"  Count: {len(data)}")
                    if len(data) > 0:
                        print(f"  Sample: {str(data[0])[:100]}...")
                else:
                    print(f"  Data: {str(data)[:100]}...")
            else:
                print(f"  Error: {r.text[:200]}")
        except Exception as e:
            print(f"  Failed: {e}")

if __name__ == "__main__":
    check_all()
