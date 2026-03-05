import sqlite3
import datetime

db_path = "bsc_bot/data/bsc_bot.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Token: CAKE
token_address = "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82"
token_name = "PancakeSwap Token"
token_symbol = "CAKE"
# Buy price low to ensure profit and no stop-loss
# Real price is ~0.0021, so 0.001 buy price means +110% profit
buy_price = 0.001 
amount = 100
buy_amount_bnb = buy_price * amount
current_time = datetime.datetime.now().timestamp()

try:
    # Clear existing simulation positions to avoid clutter
    cursor.execute("DELETE FROM simulation_positions")
    
    # Check if stop_loss_price column exists
    cursor.execute("PRAGMA table_info(simulation_positions)")
    columns = [info[1] for info in cursor.fetchall()]
    
    print(f"Columns: {columns}")

    # Prepare Insert
    # We'll use the columns we know exist or are standard
    # Based on previous context, we have:
    # token_address, token_name, buy_price_bnb, current_price, token_amount, buy_amount_bnb, status, buy_time
    # And likely: stop_loss_price, highest_price (for trailing stop)
    
    # Basic Insert
    query = """
    INSERT INTO simulation_positions (
        token_address, token_name, 
        buy_price_bnb, current_price, 
        token_amount, buy_amount_bnb, 
        status, buy_time, sold_portions
    """
    
    values = [
        token_address, token_name,
        buy_price, buy_price,
        amount, buy_amount_bnb,
        'active', current_time,
        '[]' # Empty JSON list for sold_portions
    ]
    
    placeholders = "?, ?, ?, ?, ?, ?, ?, ?, ?"
    
    # Add optional columns if they exist
    if 'token_symbol' in columns:
        query += ", token_symbol"
        placeholders += ", ?"
        values.append(token_symbol)
        
    if 'stop_loss_price' in columns:
        query += ", stop_loss_price"
        placeholders += ", ?"
        values.append(buy_price * 0.8)

    if 'take_profit_price' in columns:
        query += ", take_profit_price"
        placeholders += ", ?"
        values.append(buy_price * 2.0)
        
    if 'highest_price' in columns:
        query += ", highest_price"
        placeholders += ", ?"
        values.append(buy_price)

    query += f") VALUES ({placeholders})"
    
    cursor.execute(query, values)
    conn.commit()
    print("Injected CAKE position successfully.")
    
except Exception as e:
    print(f"Error: {e}")
finally:
    conn.close()
