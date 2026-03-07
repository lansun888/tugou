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
            # logger.info(f"Expected balance for pair: {expected_balance}")
            if expected_balance == 0:
                return -1 # Empty pair, can't verify
            
            # Try slots 0 to 20 in PARALLEL
            async def check_slot(i):
                try:
                    slot_key = self._get_storage_slot(i, pair_address)
                    storage_val = await self.w3.eth.get_storage_at(token_address, slot_key)
                    if int.from_bytes(storage_val, byteorder='big') == expected_balance:
                        return i
                except:
                    pass
                return None

            tasks = [check_slot(i) for i in range(21)]
            results = await asyncio.gather(*tasks)
            
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

        # Try slots 0 to 20 in PARALLEL
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
                
                result = await self.w3.eth.call(
                    {
                        "to": token_address,
                        "data": data
                    },
                    "latest",
                    state_override
                )
                
                val = int.from_bytes(result, byteorder='big')
                if val > 0:
                    return i
            except:
                pass
            return None

        tasks = [check_slot(i) for i in range(21)]
        results = await asyncio.gather(*tasks)
        
        for r in results:
            if r is not None:
                logger.info(f"Found allowance slot for {token_address}: {r}")
                return r
                
        return -1

    async def simulate_trade(self, token_address: str, pair_address: str, amount_bnb: float = 0.1):
        """
        Simulate Buy and Sell using eth_call.
        Returns: (is_honeypot, buy_tax, sell_tax, error_reason)
        """
        t0 = time.perf_counter()
        def _ms(t): return f"{(time.perf_counter()-t)*1000:.0f}ms"

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
            dur_parallel = _ms(t_parallel)

            logger.info(
                f"    ├─ [simulate] buy_call+find_slots 并行: {dur_parallel} "
                f"| buy_err={buy_err} balance_slot={balance_slot} allowance_slot={allowance_slot}"
            )

            if buy_err:
                logger.info(f"    └─ [simulate] 总={_ms(t0)} → 买入失败")
                return True, 0, 0, buy_err

            if balance_slot == -1 or allowance_slot == -1:
                logger.warning(f"    └─ [simulate] 总={_ms(t0)} → slot未找到，跳过simulate降级到honeypot.is")
                return False, 0, 0, "slot_not_found"

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
                logger.info(f"    └─ [simulate] sell_call={_ms(t_sell)} 总={_ms(t0)} → 卖出失败")
                return True, 0, 0, f"Sell Simulation Failed: {str(e)}"

            logger.info(f"    └─ [simulate] sell_call={_ms(t_sell)} 总={_ms(t0)} → Passed")
            return False, 0, 0, "Simulation Passed"

        except Exception as e:
            logger.error(f"Simulation error (treating as honeypot): {e}")
            return True, 0, 0, f"Simulation Error: {e}"
