import unittest
from unittest.mock import MagicMock, AsyncMock, patch
import asyncio
import time
import json
import os
import sys
import aiosqlite

# Add project root to path (bsc_bot directory)
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from executor.position_manager import PositionManager, Position

class TestFeatures(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Mock Executor
        self.executor = MagicMock()
        self.executor.config = {
            "position_management": {
                "monitor_interval": 1,
                "trailing_stop": {
                    "initial_stop_loss": -50,
                    "pullback_threshold": 40,
                    "levels": [[2.0, 50]]
                },
                "take_profit": {
                    "levels": [[100, 25]]
                },
                "time_stop": {
                    "rules": [[6, 20, 50]]
                },
                "daily_risk": {
                    "max_daily_loss": 0.5
                }
            }
        }
        # Mock executor methods
        self.executor.get_token_price = AsyncMock(return_value={'price_bnb': 1.0})
        self.executor.get_pair_liquidity = AsyncMock(return_value=100.0)
        self.executor._get_gas_price = AsyncMock(return_value=5000000000) # 5 gwei
        self.executor.sell_token = AsyncMock(return_value={"status": "success", "amount_bnb": 0.5})
        
        # Initialize PositionManager with in-memory DB for testing
        self.pm = PositionManager(self.executor, db_path=":memory:", mode="simulation")
        await self.pm._init_db()

    async def test_n_confirmation_stop_loss(self):
        print("\nTesting N-Confirmation Stop Loss...")
        
        # Setup position
        token = "0xTestStopLoss"
        pos = Position(
            token_address=token, token_name="TEST",
            buy_price_bnb=1.0, buy_amount_bnb=1.0, token_amount=1.0,
            buy_time=time.time() - 60, # Held for 1 min (Trigger Initial Stop Loss)
            highest_price=1.0, status="active",
            buy_gas_price=5000000000
        )
        self.pm.positions[token] = pos
        
        # 1. First Drop (Trigger Pending)
        # Drop 60% (below -50% initial SL)
        # Mock price to 0.4
        self.pm._get_multi_source_price = AsyncMock(return_value=(0.4, "average"))
        
        # Execute
        await self.pm._process_position(token)
        
        # Should be pending
        self.assertIn(token, self.pm.pending_stop_loss)
        first_trigger_time = self.pm.pending_stop_loss[token]["first_trigger_time"]
        
        # 2. Second Check (Too soon)
        # Advance time by 5 seconds
        with patch('time.time', return_value=first_trigger_time + 5):
            await self.pm._process_position(token)
            
            # Should still be pending
            self.assertIn(token, self.pm.pending_stop_loss)
            self.executor.sell_token.assert_not_called()
            
        # 3. Third Check (Confirmed)
        # Advance time by 20 seconds
        with patch('time.time', return_value=first_trigger_time + 20):
            await self.pm._process_position(token)
            
            # Should be sold
            self.assertNotIn(token, self.pm.pending_stop_loss)
            self.executor.sell_token.assert_called()
            print("  ✅ N-Confirmation logic passed")

    async def test_dual_source_price(self):
        print("\nTesting Dual Source Price...")
        token = "0xTestPrice"
        
        # Create a new PM instance
        pm = PositionManager(self.executor, db_path=":memory:", mode="simulation")
        await pm._init_db()
        
        # Case 1: Agreement
        self.executor.get_token_price = AsyncMock(return_value={'price_bnb': 1.0})
        
        # Mock DexScreener response
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json.return_value = {
            "pairs": [{"chainId": "bsc", "priceNative": "1.02"}]
        }
        
        # Mock aiohttp.ClientSession
        mock_session = MagicMock()
        mock_session.__aenter__.return_value = mock_session
        mock_session.__aexit__.return_value = None
        
        mock_get = MagicMock()
        mock_get.__aenter__.return_value = mock_resp
        mock_get.__aexit__.return_value = None
        
        mock_session.get.return_value = mock_get
        
        with patch('aiohttp.ClientSession', return_value=mock_session):
            price, source = await pm._get_multi_source_price(token)
            self.assertAlmostEqual(price, 1.01)
            self.assertEqual(source, "average")
            print("  ✅ Agreement case passed")
            
        # Case 2: Discrepancy > 10%
        mock_resp.json.return_value = {
            "pairs": [{"chainId": "bsc", "priceNative": "0.5"}]
        }
        
        with patch('aiohttp.ClientSession', return_value=mock_session):
            price, source = await pm._get_multi_source_price(token)
            self.assertEqual(price, 1.0) # Should trust on-chain
            self.assertEqual(source, "on_chain_only")
            print("  ✅ Discrepancy case passed")

    async def test_dynamic_slippage(self):
        print("\nTesting Dynamic Slippage...")
        token = "0xTestSlippage"
        pos = Position(
            token_address=token, token_name="TEST",
            buy_price_bnb=1.0, buy_amount_bnb=1.0, token_amount=1.0,
            buy_time=time.time(), status="active"
        )
        self.pm.positions[token] = pos
        
        # 1. High Liquidity (> 50 BNB) -> 12%
        self.executor.get_pair_liquidity = AsyncMock(return_value=60.0)
        await self.pm._execute_sell(pos, 100, "test")
        
        args, kwargs = self.executor.sell_token.call_args
        self.assertEqual(kwargs['slippage'], 12)
        print("  ✅ High liquidity slippage passed")
        
        # 2. Medium Liquidity (10-50 BNB) -> 18%
        self.executor.get_pair_liquidity = AsyncMock(return_value=20.0)
        await self.pm._execute_sell(pos, 100, "test")
        
        args, kwargs = self.executor.sell_token.call_args
        self.assertEqual(kwargs['slippage'], 18)
        print("  ✅ Medium liquidity slippage passed")

        # 3. Low Liquidity (< 10 BNB) -> 25%
        self.executor.get_pair_liquidity = AsyncMock(return_value=5.0)
        await self.pm._execute_sell(pos, 100, "test")
        
        args, kwargs = self.executor.sell_token.call_args
        self.assertEqual(kwargs['slippage'], 25)
        print("  ✅ Low liquidity slippage passed")

    async def test_gas_competition(self):
        print("\nTesting Gas Competition...")
        token = "0xTestGas"
        # Buy Gas Price: 10 Gwei
        buy_gas = 10000000000 
        pos = Position(
            token_address=token, token_name="TEST",
            buy_price_bnb=1.0, buy_amount_bnb=1.0, token_amount=1.0,
            buy_time=time.time(), status="active",
            buy_gas_price=buy_gas
        )
        
        # Current Network Gas: 6 Gwei
        self.executor._get_gas_price = AsyncMock(return_value=6000000000)
        
        # Target: max(6*1.3, 10*1.1) = max(7.8, 11) = 11 Gwei
        expected_gas = int(buy_gas * 1.1)
        
        await self.pm._execute_sell(pos, 100, "test")
        
        args, kwargs = self.executor.sell_token.call_args
        self.assertEqual(kwargs['gas_price'], expected_gas)
        print("  ✅ Gas competition logic passed")

    async def test_simulation_mode(self):
        print("\nTesting Simulation Mode...")
        self.assertEqual(self.pm.mode, "simulation")
        self.assertEqual(self.pm.positions_table, "simulation_positions")
        
        # Add position
        await self.pm.add_position("0xSim", "SIM", 1.0, 1.0, 100.0)
        
        # Check if saved to DB
        self.assertIn("0xSim", self.pm.positions)
        print("  ✅ Simulation mode passed")

if __name__ == '__main__':
    unittest.main()
