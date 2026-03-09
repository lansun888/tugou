import asyncio
import os
import sys
from web3 import AsyncWeb3, Web3
from web3.middleware import ExtraDataToPOAMiddleware
from dotenv import load_dotenv

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bsc_bot.monitor.abis import FOUR_MEME_FACTORY_ABI

# Load env
load_dotenv()

# Constants
FOUR_MEME_FACTORY = "0x5c952063c7fc8610FFDB798152D69F0B9550762b"
# RPC_URL = "https://bsc-dataseed1.binance.org" # Use a public one for testing
RPC_URL = "https://bsc-rpc.publicnode.com" # Use the one that worked

async def verify_events():
    print(f"Connecting to {RPC_URL}...")
    w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(RPC_URL))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    
    if not await w3.is_connected():
        print("Failed to connect to RPC")
        return

    print("Connected!")
    
    current_block = await w3.eth.block_number
    print(f"Current block: {current_block}")
    
    # Scan last 2000 blocks (approx 1 hour)
    from_block = current_block - 2000
    to_block = current_block
    
    print(f"Scanning for events from {FOUR_MEME_FACTORY} between {from_block} and {to_block}...")
    
    contract = w3.eth.contract(address=FOUR_MEME_FACTORY, abi=FOUR_MEME_FACTORY_ABI)
    
    # 1. Check for ANY logs from this address
    logs = await w3.eth.get_logs({
        "fromBlock": from_block,
        "toBlock": to_block,
        "address": FOUR_MEME_FACTORY
    })
    
    print(f"Found {len(logs)} raw logs.")
    
    token_create_count = 0
    listed_count = 0
    
    for log in logs:
        topic0_hex = log['topics'][0].hex()
        # Normalize to 0x prefix for display/check
        topic0 = f"0x{topic0_hex}" if not topic0_hex.startswith("0x") else topic0_hex
        
        # Try to decode as TokenCreate
        try:
            event = contract.events.TokenCreate().process_log(log)
            print(f"[TokenCreate] Token: {event['args']['token']}, Creator: {event['args']['creator']}")
            token_create_count += 1
            continue
        except Exception:
            pass
            
        # Try to decode as Listed
            try:
                event = contract.events.Listed().process_log(log)
                print(f"[Listed] Token: {event['args']['token']}, Pair: {event['args']['pair']}, Liq: {event['args']['liquidity']}")
                listed_count += 1
                continue
            except Exception:
                pass
            
            # Manual decode for 0x7db... (New TokenCreate)
            if topic0 == "0x7db52723a3b2cdd6164364b3b766e65e540d7be48ffa89582956d8eaebe62942":
                try:
                    data_hex = log['data'].hex()[2:]
                    token_hex = data_hex[0:64]
                    creator_hex = data_hex[64:128]
                    
                    token = Web3.to_checksum_address("0x" + token_hex[-40:])
                    creator = Web3.to_checksum_address("0x" + creator_hex[-40:])
                    
                    print(f"[TokenCreate (Manual)] Token: {token}, Creator: {creator}")
                    token_create_count += 1
                    continue
                except Exception as e:
                    print(f"Manual decode failed: {e}")

            # Inspect frequent unknown logs
        if topic0 in [
            "0x7db52723a3b2cdd6164364b3b766e65e540d7be48ffa89582956d8eaebe62942", 
            "0x48063b1239b68b5d50123408787a6df1f644d9160f0e5f702fefddb9a855954d",
            "0x0a5575b3648bae2210cee56bf33254cc1ddfbc7bf637c0af2ac18b14fb1bae19"
        ]:
            print(f"Unknown Log: {topic0}")
            print(f"  -> Topics: {len(log['topics'])}")
            print(f"  -> Data Length: {len(log['data'])}")
            print(f"  -> Tx Hash: {log['transactionHash'].hex()}")

    print(f"\nSummary:")
    print(f"TokenCreate events found: {token_create_count}")
    print(f"Listed events found: {listed_count}")
    
    # Verify Topics manually
    token_create_topic = "0xef0c04052959ad172ea72063a1012a3986aa06f24a6f4c41eb46103b9583390c"
    listed_topic = "0xdc896958cf16556350a89029fe81166599685cd06d043d64e4d5b3cd4df65d3b"
    
    print(f"\nExpected TokenCreate Topic: {token_create_topic}")
    print(f"Expected Listed Topic:      {listed_topic}")

if __name__ == "__main__":
    asyncio.run(verify_events())
