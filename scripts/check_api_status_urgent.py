import asyncio
import aiohttp
import os
from dotenv import load_dotenv

load_dotenv(r"d:\workSpace\tugou\.env")

async def check():
    print("Checking Network and API Status...")
    apis = {
        "GoPlus": "https://api.gopluslabs.io/api/v1/token_security/56?contract_addresses=0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
        "HoneypotIs": "https://api.honeypot.is/v2/IsHoneypot?address=0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c&chainID=56",
        "BSCScan": "https://api.bscscan.com/api?module=stats&action=bnbprice&apikey=" + (os.getenv("BSCSCAN_API_KEY") or "YourApiKeyToken"),
    }
    for name, url in apis.items():
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                    status_text = "OK" if r.status == 200 else f"Error {r.status}"
                    content = await r.text()
                    if "NOTOK" in content and name == "BSCScan":
                        status_text = f"API Error: {content[:100]}"
                    print(f"✅ {name}: {r.status} ({status_text})")
        except Exception as e:
            print(f"❌ {name}: {e}")

if __name__ == "__main__":
    asyncio.run(check())
