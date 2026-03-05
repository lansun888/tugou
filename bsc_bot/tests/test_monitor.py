import unittest
import asyncio
import sys
import os
import shutil
import tempfile
from unittest.mock import MagicMock, AsyncMock, patch

# 添加项目根目录到路径
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from monitor.pair_listener import PairListener

class TestPairListener(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # 创建临时目录存放DB
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_bot.db")
        
        # 模拟配置
        self.mock_config = {
            "network": {"ws_rpc": "wss://mock.rpc"},
            "monitor": {
                "min_liquidity_bnb": 1.0,
                "observation_wait_time": 0, # 测试时无需等待
                "competition_threshold": 3,
                "dex_enabled": {}
            },
            "database": {"path": self.db_path}
        }
        
        # Patch load_config to return mock config
        self.config_patcher = patch('monitor.pair_listener.PairListener.load_config', return_value=self.mock_config)
        self.mock_load_config = self.config_patcher.start()
        
        self.listener = PairListener("dummy_config.yaml")
        self.listener.db_path = self.db_path
        self.listener.w3 = AsyncMock()
        
        # 初始化DB
        await self.listener.init_db()

    async def asyncTearDown(self):
        self.config_patcher.stop()
        shutil.rmtree(self.test_dir)

    async def test_sensitive_word_filter(self):
        """测试敏感词过滤"""
        token_info = {
            "name": "SuperTestToken",
            "symbol": "STT",
            "total_supply_formatted": 1000
        }
        deployer = "0x123"
        
        # Test sensitive name
        token_info["name"] = "SuperTestToken" # Contains TEST
        is_valid, reason = await self.listener.check_filters(token_info, deployer)
        self.assertFalse(is_valid)
        self.assertIn("敏感词", reason)
        
        # Test sensitive symbol
        token_info["name"] = "GoodName"
        token_info["symbol"] = "SCAM"
        is_valid, reason = await self.listener.check_filters(token_info, deployer)
        self.assertFalse(is_valid)
        self.assertIn("敏感词", reason)

    async def test_supply_filter(self):
        """测试供应量过滤"""
        token_info = {
            "name": "GoodToken",
            "symbol": "GT",
            "total_supply_formatted": 10**16 # > 10^15
        }
        deployer = "0x123"
        
        is_valid, reason = await self.listener.check_filters(token_info, deployer)
        self.assertFalse(is_valid)
        self.assertIn("供应量过大", reason)

    async def test_valid_token(self):
        """测试正常代币"""
        token_info = {
            "name": "GoodToken",
            "symbol": "GT",
            "total_supply_formatted": 1000000
        }
        deployer = "0xNewDeployer"
        
        is_valid, reason = await self.listener.check_filters(token_info, deployer)
        self.assertTrue(is_valid)
        self.assertEqual(reason, "Pass")

    async def test_deployer_frequency(self):
        """测试部署者频率限制"""
        token_info = {
            "name": "GoodToken",
            "symbol": "GT",
            "total_supply_formatted": 1000000
        }
        deployer = "0xSpamDeployer"
        
        # 1. 第一次检查，应该通过
        is_valid, _ = await self.listener.check_filters(token_info, deployer)
        self.assertTrue(is_valid)
        
        # 2. 插入一条历史记录 (模拟已创建过)
        import aiosqlite
        from datetime import datetime
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO deployer_history (deployer, pair_address, created_at) VALUES (?, ?, ?)",
                (deployer, "0xPair1", datetime.now())
            )
            await db.commit()
            
        # 3. 再次检查，应该失败
        is_valid, reason = await self.listener.check_filters(token_info, deployer)
        self.assertFalse(is_valid)
        self.assertIn("部署者近期频繁创建", reason)

    async def test_analyze_competition(self):
        """测试竞争分析"""
        # Mock block data
        mock_txs = [
            {"hash": b"tx1", "value": 0},
            {"hash": b"target_tx", "value": 0}, # Our pair creation
            {"hash": b"tx2", "value": 100}, # Competitor 1
            {"hash": b"tx3", "value": 6 * 10**18}, # Whale buy (>5 BNB)
        ]
        
        # Setup mock return
        self.listener.w3.eth.get_block = AsyncMock(return_value={"transactions": mock_txs})
        
        # 注意: analyze_competition 接收的是 hex string (e.g. "0xtarget_tx")
        # 我们的 mock logic 比较的是 bytes
        # 代码中: if tx["hash"] == tx_hash_bytes:
        # 传入 "0x7461726765745f7478" (hex of b"target_tx")
        
        target_tx_hex = "0x" + b"target_tx".hex()
        
        result = await self.listener.analyze_competition(target_tx_hex, 12345)
        
        self.assertEqual(result["competitors"], 2)
        self.assertEqual(result["whale_buys"], 1)
        self.assertIn("巨鲸介入", result["risk_tags"])

if __name__ == "__main__":
    unittest.main()
