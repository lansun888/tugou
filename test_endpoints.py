import requests
import os

API_KEY = "tugou_secret_key"
BASE_URL = "http://localhost:8001/api"

endpoints = [
    "/positions",
    "/simulation/stats?days=7",
    "/trades",
    "/status"
]

for ep in endpoints:
    url = f"{BASE_URL}{ep}"
    try:
        print(f"Testing {url}...")
        response = requests.get(url, headers={"X-API-Key": API_KEY})
        print(f"Status: {response.status_code}")
        if response.status_code != 200:
            print(f"Response: {response.text}")
    except Exception as e:
        print(f"Error: {e}")
