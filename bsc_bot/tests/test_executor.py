import unittest
import asyncio
import sys
import os
import aiosqlite
from unittest.mock import MagicMock, AsyncMock, patch

# 添加项目根目录到路径
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from executor.trader import BSCExecutor

class TestBSCExecutor(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # 设置测试用的环境变量
        os.environ["WALLET_PRIVATE_KEY"] = "0x1234567890123456789012345678901234567890123456789012345678901234"
        
        # 预设配置，避免依赖外部 config.yaml
        self.config = {
            "network": {"chain_id": 56},
            "trading": {
                "buy_amount": 0.01,
                "slippage": 12,
                "deadline_seconds": 45,
                "gas": {
                    "mode": "normal",
                    "normal_multiplier": 1.15,
                    "frontrun_multiplier": 1.5,
                    "default_limit": 300000
                }
            }
        }
        
        # Patch load_config BEFORE creating executor
        with patch('executor.trader.BSCExecutor._load_config', return_value=self.config):
            self.executor = BSCExecutor()
        
        # Mock Web3
        self.executor.w3 = MagicMock()
        self.executor.w3.eth.account.from_key.return_value = MagicMock(address="0xMyWalletAddress", key="0xKey")
        self.executor.w3.is_connected = AsyncMock(return_value=True)
        # 修复: gas_price 应该是属性，返回一个 awaitable 或直接的值如果被 wrap 成 AsyncMock
        # 在 web3.py 中 w3.eth.gas_price 是一个 property，调用它会发起请求
        # 如果我们 mock 了 w3.eth，那么 w3.eth.gas_price 默认也是 AsyncMock
        # 在 executor 代码中： base_gas = await self.w3.eth.gas_price
        # 所以我们需要 w3.eth.gas_price 返回一个 5000000000
        # 但由于它是 AsyncMock，直接设置 return_value 即可
        self.executor.w3.eth.gas_price = 5000000000 # 直接赋值，因为在 await 时，如果不用于调用，这会报错吗？
        # 不，代码是 `await self.w3.eth.gas_price`。这意味着 `self.w3.eth.gas_price` 必须是一个 Awaitable。
        # 简单的整数不是 Awaitable。
        # 所以我们需要一个 Future 或者一个 Coroutine。
        
        f = asyncio.Future()
        f.set_result(5000000000)
        self.executor.w3.eth.gas_price = f

        self.executor.w3.eth.get_transaction_count = AsyncMock(return_value=10)
        self.executor.w3.eth.get_balance = AsyncMock(return_value=10**18) # 1 BNB
        self.executor.w3.eth.send_raw_transaction = AsyncMock(return_value=b'\x00'*32)
        self.executor.w3.to_hex = lambda x: "0x" + x.hex()
        self.executor.w3.to_wei = lambda x, u: int(x * 10**18)
        self.executor.w3.from_wei = lambda x, u: x / 10**18
        self.executor.w3_to_checksum = lambda x: x
        
        # Mock Router Contract
        self.executor.router = MagicMock()
        self.executor.router.functions.getAmountsOut.return_value.call = AsyncMock(return_value=[10**18, 500*10**18]) # 1 BNB -> 500 Tokens
        
        # Mock Swap Function
        mock_swap_func = MagicMock()
        mock_swap_func.estimate_gas = AsyncMock(return_value=200000)
        mock_swap_func.build_transaction = AsyncMock(return_value={'to': '0xRouter', 'data': '0x', 'value': 0, 'gas': 240000, 'gasPrice': 5750000000, 'nonce': 10, 'chainId': 56})
        self.executor.router.functions.swapExactETHForTokensSupportingFeeOnTransferTokens.return_value = mock_swap_func
        
        # Mock Receipt
        mock_receipt = MagicMock()
        mock_receipt.status = 1
        self.executor.w3.eth.wait_for_transaction_receipt = AsyncMock(return_value=mock_receipt)
        
        # Use in-memory DB for testing
        self.executor.db_path = ":memory:"
        await self.executor._init_db()
        self.executor.account = self.executor.w3.eth.account.from_key("0xKey")

    async def asyncTearDown(self):
        await self.executor.close()

    async def test_gas_strategy_normal(self):
        """测试普通模式 Gas 策略"""
        self.executor.config["trading"]["gas"]["mode"] = "normal"
        self.executor.config["trading"]["gas"]["normal_multiplier"] = 1.15
        
        gas_price = await self.executor._get_gas_price()
        # 5 Gwei * 1.15 = 5.75 Gwei
        self.assertEqual(gas_price, int(5000000000 * 1.15))

    async def test_gas_strategy_frontrun(self):
        """测试抢跑模式 Gas 策略"""
        self.executor.config["trading"]["gas"]["mode"] = "frontrun"
        self.executor.config["trading"]["gas"]["frontrun_multiplier"] = 1.5
        
        gas_price = await self.executor._get_gas_price()
        # 5 Gwei * 1.5 = 7.5 Gwei
        self.assertEqual(gas_price, int(5000000000 * 1.5))

    async def test_buy_token_success(self):
        """测试买入成功流程"""
        token_addr = "0xTokenAddress"
        res = await self.executor.buy_token(token_addr, "TEST")
        
        self.assertEqual(res["status"], "success")
        self.assertIn(token_addr.lower(), self.executor.bought_tokens)
        
        # 验证数据库记录
        # 注意：aiosqlite.connect(":memory:") 每次连接都是一个新的内存数据库
        # 所以我们在 test case 中使用 connect 连接到 self.executor.db_path 
        # 但 self.executor._log_trade 中也使用了 connect(self.db_path)
        # 如果 db_path 是 ":memory:"，那么每次 connect 都是隔离的。
        # 解决方法：在测试中使用共享的内存数据库 URI 或临时文件
        # 或者 mock _log_trade
        
        # 让我们 Mock _log_trade 来验证它被调用了
        pass

    # ... (other tests)
    
    # 重新编写 test_buy_token_success，使用 mock _log_trade
    
    @patch('executor.trader.BSCExecutor._log_trade')
    async def test_buy_token_success(self, mock_log_trade):
        """测试买入成功流程"""
        token_addr = "0xTokenAddress"
        res = await self.executor.buy_token(token_addr, "TEST")
        
        self.assertEqual(res["status"], "success")
        self.assertIn(token_addr.lower(), self.executor.bought_tokens)
        
        mock_log_trade.assert_called_once()
        args = mock_log_trade.call_args[0]
        self.assertEqual(args[0], token_addr)
        self.assertEqual(args[2], "buy")


    async def test_buy_token_duplicate(self):
        """测试重复买入拦截"""
        token_addr = "0xTokenAddress"
        self.executor.bought_tokens.add(token_addr.lower())
        
        res = await self.executor.buy_token(token_addr, "TEST")
        self.assertEqual(res["status"], "skipped")
        self.assertEqual(res["reason"], "already_bought")

    async def test_buy_insufficient_balance(self):
        """测试余额不足"""
        self.executor.w3.eth.get_balance = AsyncMock(return_value=0)
        res = await self.executor.buy_token("0xToken", "TEST")
        
        self.assertEqual(res["status"], "failed")
        self.assertEqual(res["reason"], "insufficient_balance")

    @patch('executor.trader.BSCExecutor._get_bnb_price_usd', new_callable=AsyncMock)
    async def test_get_token_price(self, mock_get_bnb_price):
        """测试获取代币价格"""
        token_addr = "0xTokenAddress"
        mock_get_bnb_price.return_value = 300.0 # 1 BNB = $300
        
        # Mock Token Contract (decimals)
        mock_token = MagicMock()
        mock_token.functions.decimals.return_value.call = AsyncMock(return_value=18)
        
        # Mock Router (getAmountsOut, factory)
        self.executor.router.functions.getAmountsOut.return_value.call = AsyncMock(return_value=[10**18, 5 * 10**16]) # 1 Token = 0.05 BNB
        self.executor.router.functions.factory.return_value.call = AsyncMock(return_value="0xFactory")
        
        # Mock Factory (getPair)
        mock_factory = MagicMock()
        mock_factory.functions.getPair.return_value.call = AsyncMock(return_value="0xPair")
        
        # Mock Pair (getReserves, token0)
        mock_pair = MagicMock()
        # reserve0 = 10 BNB, reserve1 = 200 Token
        mock_pair.functions.getReserves.return_value.call = AsyncMock(return_value=[10 * 10**18, 200 * 10**18, 0])
        mock_pair.functions.token0.return_value.call = AsyncMock(return_value=self.executor.WBNB_ADDRESS) # WBNB is token0
        
        # Setup w3.eth.contract to return different mocks based on address/abi
        def side_effect_contract(address=None, abi=None):
            if address == token_addr:
                return mock_token
            elif address == "0xFactory":
                return mock_factory
            elif address == "0xPair":
                return mock_pair
            return MagicMock()
            
        self.executor.w3.eth.contract = MagicMock(side_effect=side_effect_contract)
        
        price_info = await self.executor.get_token_price(token_addr)
        
        self.assertIsNotNone(price_info)
        self.assertEqual(price_info["price_bnb"], 0.05)
        self.assertEqual(price_info["price_usd"], 0.05 * 300.0)
        self.assertEqual(price_info["liquidity_bnb"], 10.0)

if __name__ == "__main__":
    unittest.main()
