import asyncio
import logging
from web3 import AsyncWeb3, Web3
from eth_utils import keccak, to_checksum_address
from eth_abi import encode
import time

logger = logging.getLogger(__name__)

# Constants
ROUTER_ADDRESS = "0x10ED43C718714eb63d5aA57B78B54704E256024E"  # PancakeSwap V2 Router
WBNB_ADDRESS = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
SIMULATOR_ADDRESS = "0x0000000000000000000000000000000000001234"  # Fake address

# ABI Snippets
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [
            {"name": "_owner", "type": "address"},
            {"name": "_spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
]

ROUTER_ABI = [
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"},
        ],
        "name": "swapExactETHForTokensSupportingFeeOnTransferTokens",
        "outputs": [],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"},
        ],
        "name": "swapExactTokensForETHSupportingFeeOnTransferTokens",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
        ],
        "name": "getAmountsOut",
        "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
        "stateMutability": "view",
        "type": "function",
    }
]

class LocalSimulator:
    def __init__(self, w3: AsyncWeb3):
        self.w3 = w3
        self.router = self.w3.eth.contract(address=ROUTER_ADDRESS, abi=ROUTER_ABI)
        
        # Use a synchronous Web3 instance for offline transaction encoding to avoid balance checks
        self.sync_w3 = Web3()
        self.sync_router = self.sync_w3.eth.contract(address=ROUTER_ADDRESS, abi=ROUTER_ABI)

    def _encode_call_data(self, fn):
        if hasattr(fn, "encodeABI"):
            return fn.encodeABI()
        if hasattr(fn, "encode_abi"):
            return fn.encode_abi()
        if hasattr(fn, "_encode_transaction_data"):
            return fn._encode_transaction_data()
        raise AttributeError("Function encoding not supported")

    def _get_storage_slot(self, mapping_slot: int, key_address: str) -> str:
        """Calculate storage slot for mapping[key]"""
        # keccak256(abi.encode(key, slot))
        encoded = encode(['address', 'uint256'], [to_checksum_address(key_address), mapping_slot])
        return '0x' + keccak(encoded).hex()

    def _get_nested_storage_slot(self, mapping_slot: int, key1: str, key2: str) -> str:
        """Calculate storage slot for mapping[key1][key2]"""
        # slot1 = keccak256(abi.encode(key1, mapping_slot))
        # slot2 = keccak256(abi.encode(key2, slot1)) (Note: Solidity uses the slot value, not the index?)
        # Actually standard nested mapping is:
        # keccak256(abi.encode(key2, keccak256(abi.encode(key1, mapping_slot))))
        slot1_bytes = keccak(encode(['address', 'uint256'], [to_checksum_address(key1), mapping_slot]))
        slot2 = keccak(encode(['address', 'bytes32'], [to_checksum_address(key2), slot1_bytes]))
        return '0x' + slot2.hex()

    async def find_balance_slot(self, token_address: str, pair_address: str) -> int:
        """Find the storage slot for balanceOf using Pair balance as reference."""
        token_address = to_checksum_address(token_address)
        pair_address = to_checksum_address(pair_address)
        token = self.w3.eth.contract(address=token_address, abi=ERC20_ABI)
        try:
            expected_balance = await token.functions.balanceOf(pair_address).call()
            if expected_balance == 0:
                return -1 # Empty pair, can't verify

            # Try slots 0 to 20 in PARALLEL, each with 200ms per-slot timeout
            async def check_slot(i):
                try:
                    slot_key = self._get_storage_slot(i, pair_address)
                    storage_val = await asyncio.wait_for(
                        self.w3.eth.get_storage_at(token_address, slot_key),
                        timeout=0.2
                    )
                    if int.from_bytes(storage_val, byteorder='big') == expected_balance:
                        return i
                except (asyncio.TimeoutError, Exception):
                    pass
                return None

            tasks = [check_slot(i) for i in range(21)]
            try:
                results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=3.0)
            except asyncio.TimeoutError:
                logger.warning(f"find_balance_slot 整体超时(>3s): {token_address[:10]}")
                return -1

            for r in results:
                if r is not None:
                    logger.info(f"Found balance slot for {token_address}: {r}")
                    return r
            return -1
        except Exception as e:
            logger.error(f"Error finding balance slot: {e}")
            return -1

    async def find_allowance_slot(self, token_address: str) -> int:
        """Find the storage slot for allowance using state override simulation."""
        token_address = to_checksum_address(token_address)
        token = self.w3.eth.contract(address=token_address, abi=ERC20_ABI)
        
        spender = ROUTER_ADDRESS
        owner = SIMULATOR_ADDRESS
        max_uint = "0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        
        # Pre-build transaction data
        try:
            # Note: gasPrice 0 to avoid balance checks if any
            tx = await token.functions.allowance(owner, spender).build_transaction({'gas': 100000, 'gasPrice': 0})
            data = tx['data']
        except Exception as e:
            logger.warning(f"Failed to build allowance tx: {e}")
            return -1

        # Try slots 0 to 20 in PARALLEL, each with 200ms per-slot timeout
        async def check_slot(i):
            try:
                slot_key = self._get_nested_storage_slot(i, owner, spender)
                state_override = {
                    token_address: {
                        "stateDiff": {
                            slot_key: max_uint
                        }
                    }
                }
                result = await asyncio.wait_for(
                    self.w3.eth.call(
                        {"to": token_address, "data": data},
                        "latest",
                        state_override
                    ),
                    timeout=0.2
                )
                val = int.from_bytes(result, byteorder='big')
                if val > 0:
                    return i
            except (asyncio.TimeoutError, Exception):
                pass
            return None

        tasks = [check_slot(i) for i in range(21)]
        try:
            results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=3.0)
        except asyncio.TimeoutError:
            logger.warning(f"find_allowance_slot 整体超时(>3s): {token_address[:10]}")
            return -1

        for r in results:
            if r is not None:
                logger.info(f"Found allowance slot for {token_address}: {r}")
                return r

        return -1

    async def simulate_trade(self, token_address: str, pair_address: str, amount_bnb: float = 0.1):
        """
        Simulate Buy and Sell using eth_call.
        Returns: (is_honeypot, buy_tax, sell_tax, error_reason, timing_stats)
        """
        t0 = time.perf_counter()
        timing_stats = {}
        def _dur(t): return (time.perf_counter() - t) * 1000

        try:
            token_address = to_checksum_address(token_address)
            pair_address = to_checksum_address(pair_address)

            amount_in_wei = self.w3.to_wei(amount_bnb, 'ether')

            # Encode buy/sell calldata (sync, zero latency)
            buy_fn = self.sync_router.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
                0,
                [WBNB_ADDRESS, token_address],
                SIMULATOR_ADDRESS,
                int(time.time()) + 1200
            )
            buy_data = self._encode_call_data(buy_fn)
            buy_tx = {
                'from': SIMULATOR_ADDRESS,
                'to': ROUTER_ADDRESS,
                'value': amount_in_wei,
                'gas': 500000,
                'gasPrice': 0,
                'data': buy_data
            }
            buy_state_override = {
                to_checksum_address(SIMULATOR_ADDRESS): {
                    "balance": "0x56BC75E2D63100000"
                }
            }

            # ── 并行执行：buy_call + find_balance_slot + find_allowance_slot ──
            t_parallel = time.perf_counter()
            async def _buy_call():
                try:
                    await self.w3.eth.call(buy_tx, "latest", buy_state_override)
                    return None  # success
                except Exception as e:
                    return f"Buy Simulation Failed: {str(e)}"

            buy_err, balance_slot, allowance_slot = await asyncio.gather(
                _buy_call(),
                self.find_balance_slot(token_address, pair_address),
                self.find_allowance_slot(token_address),
            )
            timing_stats["buy_step_ms"] = _dur(t_parallel)
            
            if buy_err:
                return True, 0, 0, buy_err, timing_stats

            if balance_slot == -1 or allowance_slot == -1:
                return False, 0, 0, "slot_not_found", timing_stats

            # ── Sell simulation ──
            t_sell = time.perf_counter()
            amount_tokens_to_sell = 10**18
            bal_key = self._get_storage_slot(balance_slot, SIMULATOR_ADDRESS)
            allow_key = self._get_nested_storage_slot(allowance_slot, SIMULATOR_ADDRESS, ROUTER_ADDRESS)
            max_uint = "0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
            amount_hex = "0x" + amount_tokens_to_sell.to_bytes(32, 'big').hex()

            state_override = {
                token_address: {
                    "stateDiff": {
                        bal_key: amount_hex,
                        allow_key: max_uint
                    }
                },
                to_checksum_address(SIMULATOR_ADDRESS): {
                    "balance": "0x56BC75E2D63100000"
                }
            }

            sell_fn = self.sync_router.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
                amount_tokens_to_sell,
                0,
                [token_address, WBNB_ADDRESS],
                SIMULATOR_ADDRESS,
                int(time.time()) + 1200
            )
            sell_data = self._encode_call_data(sell_fn)
            sell_tx = {
                'from': SIMULATOR_ADDRESS,
                'to': ROUTER_ADDRESS,
                'value': 0,
                'gas': 500000,
                'gasPrice': 0,
                'data': sell_data
            }

            try:
                await self.w3.eth.call(sell_tx, "latest", state_override)
            except Exception as e:
                timing_stats["sell_step_ms"] = _dur(t_sell)
                return True, 0, 0, f"Sell Simulation Failed: {str(e)}", timing_stats

            timing_stats["sell_step_ms"] = _dur(t_sell)
            timing_stats["total_ms"] = _dur(t0)
            return False, 0, 0, "Simulation Passed", timing_stats

        except Exception as e:
            logger.error(f"Simulation error (treating as honeypot): {e}")
            return True, 0, 0, f"Simulation Error: {e}", timing_stats

    async def simulate_buy(self, token_address: str, amount_bnb: float = 0.0001):
        """
        模拟买入操作，返回预期获得的代币数量
        用于貔貅检测的预检测阶段
        """
        try:
            token_address = to_checksum_address(token_address)
            amount_in_wei = self.w3.to_wei(amount_bnb, 'ether')

            # 编码买入交易
            buy_fn = self.sync_router.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
                0,
                [WBNB_ADDRESS, token_address],
                SIMULATOR_ADDRESS,
                int(time.time()) + 1200
            )
            buy_data = self._encode_call_data(buy_fn)
            buy_tx = {
                'from': SIMULATOR_ADDRESS,
                'to': ROUTER_ADDRESS,
                'value': amount_in_wei,
                'gas': 500000,
                'gasPrice': 0,
                'data': buy_data
            }
            buy_state_override = {
                to_checksum_address(SIMULATOR_ADDRESS): {
                    "balance": "0x56BC75E2D63100000"
                }
            }

            # 执行模拟买入
            await self.w3.eth.call(buy_tx, "latest", buy_state_override)

            # 获取预期输出数量
            try:
                amounts_out = await self.router.functions.getAmountsOut(
                    amount_in_wei,
                    [WBNB_ADDRESS, token_address]
                ).call()
                received_amount = amounts_out[1] if len(amounts_out) > 1 else 0
            except:
                received_amount = 10**18  # 默认1个代币用于后续卖出测试

            return {
                "success": True,
                "received_amount": received_amount,
                "status": "success"
            }

        except Exception as e:
            logger.warning(f"模拟买入失败: {str(e)}")
            return {
                "success": False,
                "received_amount": 0,
                "status": "revert",
                "revert_reason": str(e)
            }

    async def simulate_sell(self, token_address: str, amount_token: int, pair_address: str = None):
        """
        模拟卖出操作，检测是否能成功卖出
        用于貔貅检测的核心环节

        Args:
            token_address: 代币地址
            amount_token: 卖出数量
            pair_address: 交易对地址（用于查找存储槽位）
        """
        try:
            token_address = to_checksum_address(token_address)

            # 查找 balance 和 allowance slot
            # 如果没有 pair_address，使用 WBNB 作为参考（可能不准确）
            ref_address = pair_address if pair_address else WBNB_ADDRESS
            balance_slot = await self.find_balance_slot(token_address, ref_address)
            allowance_slot = await self.find_allowance_slot(token_address)

            if balance_slot == -1 or allowance_slot == -1:
                return {
                    "success": False,
                    "status": "revert",
                    "revert_reason": "无法找到存储槽位",
                    "effective_tax": 1.0
                }

            # 构造状态覆盖
            bal_key = self._get_storage_slot(balance_slot, SIMULATOR_ADDRESS)
            allow_key = self._get_nested_storage_slot(allowance_slot, SIMULATOR_ADDRESS, ROUTER_ADDRESS)
            max_uint = "0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
            amount_hex = "0x" + amount_token.to_bytes(32, 'big').hex()

            state_override = {
                token_address: {
                    "stateDiff": {
                        bal_key: amount_hex,
                        allow_key: max_uint
                    }
                },
                to_checksum_address(SIMULATOR_ADDRESS): {
                    "balance": "0x56BC75E2D63100000"
                }
            }

            # 编码卖出交易
            sell_fn = self.sync_router.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
                amount_token,
                0,
                [token_address, WBNB_ADDRESS],
                SIMULATOR_ADDRESS,
                int(time.time()) + 1200
            )
            sell_data = self._encode_call_data(sell_fn)
            sell_tx = {
                'from': SIMULATOR_ADDRESS,
                'to': ROUTER_ADDRESS,
                'value': 0,
                'gas': 500000,
                'gasPrice': 0,
                'data': sell_data
            }

            # 执行模拟卖出
            await self.w3.eth.call(sell_tx, "latest", state_override)

            # 计算有效税率（通过 getAmountsOut 对比）
            try:
                amounts_out = await self.router.functions.getAmountsOut(
                    amount_token,
                    [token_address, WBNB_ADDRESS]
                ).call()
                expected_bnb = amounts_out[1] if len(amounts_out) > 1 else 0

                # 简化税率计算：如果能成功调用，税率视为正常
                effective_tax = 0.0
            except:
                effective_tax = 0.0

            return {
                "success": True,
                "status": "success",
                "effective_tax": effective_tax,
                "revert_reason": None
            }

        except Exception as e:
            error_msg = str(e)
            logger.warning(f"模拟卖出失败: {error_msg}")

            # 解析税率（如果错误信息中包含）
            effective_tax = 1.0  # 默认100%税率（完全无法卖出）

            return {
                "success": False,
                "status": "revert",
                "revert_reason": error_msg,
                "effective_tax": effective_tax
            }
