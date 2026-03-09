import sqlite3
import os

db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'blacklist.db')
print(f"DB路径: {db_path}")
print(f"DB存在: {os.path.exists(db_path)}")

conn = sqlite3.connect(db_path)
cur = conn.cursor()

cur.execute("SELECT COUNT(*) FROM blacklist_code_hash WHERE reason LIKE '%Storage slots not found%' OR reason LIKE '%Simulation Failed%'")
hash_count = cur.fetchone()[0]
print(f"blacklist_code_hash 待删除: {hash_count} 条")

cur.execute("SELECT COUNT(*) FROM blacklist_deployer WHERE reason LIKE '%Deployed Honeypot%'")
dep_count = cur.fetchone()[0]
print(f"blacklist_deployer Deployed Honeypot 记录: {dep_count} 条 (保留，可能含真实貔貅部署者)")

cur.execute("DELETE FROM blacklist_code_hash WHERE reason LIKE '%Storage slots not found%' OR reason LIKE '%Simulation Failed%'")
deleted = cur.rowcount
conn.commit()
conn.close()
print(f"已删除 blacklist_code_hash 误加记录: {deleted} 条")
