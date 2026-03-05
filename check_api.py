import requests
import json

try:
    response = requests.get(
        "http://localhost:8002/api/positions",
        headers={"x-api-key": "tugou_secret_key"}
    )
    print(json.dumps(response.json(), indent=2))
except Exception as e:
    print(e)
