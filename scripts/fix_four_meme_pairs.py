import asyncio
import sqlite3
from web3 import Web3

DB_PATH = r"d:\workSpace\tugou\bsc_bot\data\bsc_bot.db"
RPC = "https://1rpc.io/bnb"
PANCAKE_FACTORY = "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"
WBNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
ZERO = '0x' + '0' * 40

FACTORY_ABI = [{
    "name": "getPair",
    "inputs": [
        {"name": "tokenA", "type": "address"},
        {"name": "tokenB", "type": "address"}
    ],
    "outputs": [{"name": "pair", "type": "address"}],
    "stateMutability": "view",
    "type": "function"
}]

async def fix():
    try:
        w3 = Web3(Web3.HTTPProvider(RPC))
        if not w3.is_connected():
            print("无法连接到RPC")
            return

        factory = w3.eth.contract(address=PANCAKE_FACTORY, abi=FACTORY_ABI)
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Determine platform column name
        cursor.execute("PRAGMA table_info(simulation_positions)")
        columns = [col[1] for col in cursor.fetchall()]
        platform_col = 'platform' if 'platform' in columns else 'dex_name'
        print(f"Using platform column: {platform_col}")
        
        # 获取所有pair_address为空的Four.meme持仓
        query = f"""
            SELECT token_address, token_name 
            FROM simulation_positions
            WHERE {platform_col}='four_meme' 
            AND status='active'
            AND (pair_address IS NULL OR pair_address = '' OR pair_address = 'None')
        """
        cursor.execute(query)
        positions = cursor.fetchall()
        print(f"需要修复的持仓数量: {len(positions)}")
        
        fixed = 0
        failed = 0
        
        for token_address, token_name in positions:
            try:
                # Use synchronous call since Web3 is synchronous here
                pair = factory.functions.getPair(
                    Web3.to_checksum_address(token_address),
                    WBNB
                ).call()
                
                if pair and pair != ZERO:
                    cursor.execute("""
                        UPDATE simulation_positions 
                        SET pair_address = ?
                        WHERE token_address = ?
                    """, (pair, token_address))
                    conn.commit()
                    print(f"✅ {token_name}: {pair[:10]}...")
                    fixed += 1
                else:
                    print(f"❌ {token_name}: pair不存在（可能已归零）")
                    # 标记为已关闭
                    cursor.execute("""
                        UPDATE simulation_positions
                        SET status='closed'
                        WHERE token_address = ?
                    """, (token_address,))
                    conn.commit()
                    failed += 1
                    
            except Exception as e:
                print(f"❌ {token_name}: 查询失败 {e}")
                failed += 1
            
            await asyncio.sleep(0.2)  # 避免RPC限速
        
        conn.close()
        print(f"\n修复完成: 成功{fixed}个 失败{failed}个")

    except Exception as e:
        print(f"Fatal Error: {e}")

if __name__ == "__main__":
    asyncio.run(fix())
