import sqlite3
try:
    conn = sqlite3.connect('d:/workSpace/tugou/bsc_bot/data/bsc_bot.db')
    cursor = conn.cursor()
    cursor.execute('PRAGMA table_info(trades)')
    print('TRADES:', cursor.fetchall())
    cursor.execute('PRAGMA table_info(simulation_trades)')
    print('SIM_TRADES:', cursor.fetchall())
    cursor.execute('PRAGMA table_info(positions)')
    print('POSITIONS:', cursor.fetchall())
    cursor.execute('PRAGMA table_info(simulation_positions)')
    print('SIM_POSITIONS:', cursor.fetchall())
    conn.close()
except Exception as e:
    print(f"Error: {e}")
