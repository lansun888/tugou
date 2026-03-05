import asyncio
import os
import sys
from datetime import datetime
from web3 import Web3

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bsc_bot.monitor.pair_listener import PairListener

async def verify_processing():
    # 1. Mock config loading
    PairListener.load_config = lambda self, path: {
        "network": {"private_rpcs": []},
        "monitor": {"observation_wait_time": 0, "min_liquidity_bnb": 0}
    }
    
    # 2. Mock Web3 setup
    async def mock_setup_web3(self):
        print("Mocking Web3 setup...")
        # Create a dummy w3 object just in case
        # Mock eth.get_block for latency calculation
        async def get_block(block_number):
            return {"timestamp": datetime.now().timestamp() - 1} # 1 second ago

        self.w3 = type('obj', (object,), {
            'provider': type('obj', (object,), {'disconnect': lambda: None}),
            'eth': type('obj', (object,), {'get_block': get_block})
        })
    PairListener.setup_web3 = mock_setup_web3

    listener = PairListener(db_path="./data/test_bsc_bot.db")
    
    # Initialize DB
    await listener.init_db()
    await listener.setup_web3()
    
    # 3. Mock get_token_info
    async def mock_get_token_info(token_address):
        return {
            "address": token_address,
            "name": "Test Token",
            "symbol": "TEST",
            "decimals": 18,
            "total_supply": 1000000 * 10**18,
            "total_supply_formatted": 1000000
        }
    listener.get_token_info = mock_get_token_info
    
    # 4. Mock observe_liquidity (although we modified it to return True for four_meme)
    # The code in pair_listener.py already handles "four_meme" by returning True immediately.
    # So we might not need to mock it if we pass "four_meme" as dex_name.
    # But let's be safe.
    
    # 5. Mock deployer
    async def mock_deployer(tx):
        return "0x0000000000000000000000000000000000001234"
    listener.get_deployer = mock_deployer

    # 6. Mock analyze competition
    async def mock_analyze(tx, block):
        return {"risk": False, "competitors": 0, "whale_buys": 0, "risk_tags": []}
    listener.analyze_competition = mock_analyze
    
    token_address = "0x2aebb93d8314219fcc0ac4b95227024a31dd4444"
    creator_address = "0x0000000000000000000000000000000000001234"
    
    mock_event = {
        "args": {
            "token": token_address,
            "creator": creator_address
        },
        "transactionHash": bytes.fromhex("1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"),
        "blockNumber": 12345678
    }
    
    print(f"Testing processing for token: {token_address}")
    start_time = datetime.now()
    
    # process_event expects 3 args: event, dex_name, factory_type
    await listener.process_event(mock_event, "four_meme", "TokenCreate")
    
    # Wait a bit for async liquidity check task to complete
    await asyncio.sleep(2)
    
    end_time = datetime.now()
    print(f"Processing time: {(end_time - start_time).total_seconds() * 1000:.2f} ms")
    
    # Verify DB
    import aiosqlite
    async with aiosqlite.connect(listener.db_path) as db:
        checksum_address = Web3.to_checksum_address(token_address)
        print(f"Querying DB for address: {checksum_address}")
        async with db.execute("SELECT * FROM pairs WHERE pair_address = ?", (checksum_address,)) as cursor:
            row = await cursor.fetchone()
            if row:
                print("SUCCESS: Token found in DB!")
                print(f"Row: {row}")
            else:
                print("FAILURE: Token not found in DB.")
                # Debug: show all pairs
                async with db.execute("SELECT pair_address FROM pairs") as cursor_all:
                     rows = await cursor_all.fetchall()
                     print(f"All pairs in DB: {rows}")

    await listener._close_provider(listener.w3.provider)

if __name__ == "__main__":
    asyncio.run(verify_processing())
