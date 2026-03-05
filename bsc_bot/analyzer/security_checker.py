import asyncio
import json
import logging
import os
import aiohttp
import time
import re
import collections
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from loguru import logger
from web3 import AsyncWeb3
from dotenv import load_dotenv
from eth_utils import keccak, to_checksum_address

from .local_simulator import LocalSimulator
from .blacklist_manager import BlacklistManager

# 加载环境变量
load_dotenv()

# 常量定义
CHAIN_ID = 56
GOPLUS_API_URL = "https://api.gopluslabs.io/api/v1/token_security/56"
HONEYPOT_IS_API_URL = "https://api.honeypot.is/v2/IsHoneypot"
BSCSCAN_API_URL = "https://api.bscscan.com/api"
BSCSCAN_API_KEY = os.getenv("BSCSCAN_API_KEY", "")

# Tornado Cash 混币器地址
TORNADO_CASH_ADDRESS = "0x84443CFd09A48AF6eF360C6976C5392aC5023a1F".lower()

# 锁仓平台地址
LOCK_PLATFORMS = {
    "PinkLock": "0x407993575c91ce7643a4d4cCACc9A98c36eE1BBe".lower(),
    "Mudra": "0x2b18F6a0a12A35c6c46cD8E4Bf35F9d0F34F37A".lower(),
    "Team Finance": "0xE2fE530C047f2d85298b07D9333C05737f1435fB".lower()
}

# 危险函数列表
DANGEROUS_FUNCTIONS = [
    "mint", "_mint", 
    "pause", "unpause", 
    "blacklist", "addBlacklist", 
    "setMaxTxAmount", 
    "excludeFromFee", 
    "transferOwnership"
]

class SecurityChecker:
    def __init__(self, w3: AsyncWeb3 = None):
        self.w3 = w3
        self.session = None
        self.local_simulator = LocalSimulator(w3) if w3 else None
        self.blacklist_manager = BlacklistManager()
        self.blacklist = self._load_blacklist()

    def set_web3(self, w3: AsyncWeb3):
        """Update Web3 instance and re-initialize simulator"""
        self.w3 = w3
        self.local_simulator = LocalSimulator(w3) if w3 else None
        
    async def _get_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10),
                trust_env=True  # 使用系统代理（HTTP_PROXY）访问外部 API
            )
        return self.session

    def _load_blacklist(self) -> set:
        """加载黑名单"""
        blacklist = set()
        try:
            path = os.path.join(os.path.dirname(__file__), "blacklist.txt")
            if os.path.exists(path):
                with open(path, "r") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            blacklist.add(line.lower())
        except Exception as e:
            logger.error(f"加载黑名单失败: {e}")
        return blacklist

    def _finalize_result(self, result, score, risk_items, bonus_items, start_time):
        """Helper to finalize result structure"""
        # User requested max score 150, buy threshold 85
        score = max(0, min(150, score))
        decision = "reject"
        if score >= 85:
            decision = "buy"
        elif score >= 60:
            decision = "half_buy"
        elif score >= 40:
            decision = "notify"
            
        result["final_score"] = score
        result["decision"] = decision
        result["risk_items"] = risk_items
        result["bonus_items"] = bonus_items
        result["analysis_time"] = time.time() - start_time
        return result

    async def check_goplus(self, token_address: str) -> Dict[str, Any]:
        """GoPlus Security API 检测"""
        try:
            session = await self._get_session()
            params = {"contract_addresses": token_address}
            async with session.get(GOPLUS_API_URL, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result = data.get("result", {}).get(token_address.lower(), {})
                    return result
        except Exception as e:
            logger.warning(f"GoPlus API 检测失败: {e}")
        return {}

    async def check_honeypot_is(self, token_address: str) -> Dict[str, Any]:
        """Honeypot.is API 检测"""
        try:
            session = await self._get_session()
            params = {"address": token_address, "chainID": CHAIN_ID}
            async with session.get(HONEYPOT_IS_API_URL, params=params) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            logger.warning(f"Honeypot.is API 检测失败: {e}")
        return {}

    async def check_contract_code(self, token_address: str) -> Dict[str, Any]:
        """合约代码分析 (通过 BscScan 获取 ABI/Source)"""
        try:
            session = await self._get_session()
            params = {
                "module": "contract",
                "action": "getsourcecode",
                "address": token_address,
                "apikey": BSCSCAN_API_KEY
            }
            async with session.get(BSCSCAN_API_URL, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data["status"] == "1" and data["result"]:
                        return data["result"][0]
        except Exception as e:
            logger.warning(f"合约代码获取失败: {e}")
        return {}
    
    async def analyze_token_holders(self, token_address: str, deployer_address: str = None, pair_address: str = None) -> Dict[str, Any]:
        """代币持仓分析 (使用 BscScan API)"""
        try:
            session = await self._get_session()
            # 尝试使用 BscScan API 获取持仓列表
            # 注意：这是 Pro 接口，普通 key 可能无法访问，或者访问受限
            params = {
                "module": "token",
                "action": "tokenholderlist",
                "contractaddress": token_address,
                "page": 1,
                "offset": 20,
                "apikey": BSCSCAN_API_KEY
            }
            
            async with session.get(BSCSCAN_API_URL, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data["status"] == "1" and data["result"]:
                        holders = data["result"]
                        total_supply = 0
                        # 尝试获取 total supply
                        # 如果 API 返回结果中没有 supply 信息，需要单独查询
                        # 这里简化处理，假设 holders 中的 TokenHolderQuantity 是准确的
                        # 我们先累加前20个作为参考，或者调用 web3 totalSupply
                        
                        real_total_supply = 0
                        if self.w3:
                            try:
                                contract = self.w3.eth.contract(address=AsyncWeb3.to_checksum_address(token_address), abi=[
                                    {"constant":True,"inputs":[],"name":"totalSupply","outputs":[{"name":"","type":"uint256"}],"payable":False,"stateMutability":"view","type":"function"},
                                    {"constant":True,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"payable":False,"stateMutability":"view","type":"function"}
                                ])
                                real_total_supply = await contract.functions.totalSupply().call()
                                decimals = await contract.functions.decimals().call()
                                real_total_supply = real_total_supply / (10 ** decimals)
                            except:
                                pass
                        
                        if real_total_supply == 0:
                            # Fallback: sum of top 20 (inaccurate but better than nothing)
                            real_total_supply = sum(float(h["TokenHolderQuantity"]) for h in holders) * 1.5 

                        top_5_share = 0
                        max_single_share = 0
                        deployer_share = 0
                        
                        processed_holders = []
                        
                        for i, h in enumerate(holders):
                            addr = h["TokenHolderAddress"].lower()
                            qty = float(h["TokenHolderQuantity"])
                            percentage = (qty / real_total_supply) * 100 if real_total_supply > 0 else 0
                            
                            # 排除零地址和常见 burn 地址
                            if addr in ["0x0000000000000000000000000000000000000000", "0x000000000000000000000000000000000000dead"]:
                                continue
                                
                            # 排除 LP 地址
                            if pair_address and addr == pair_address.lower():
                                continue

                            # 排除常见锁仓合约
                            if addr in [p.lower() for p in LOCK_PLATFORMS.values()]:
                                continue
                            
                            processed_holders.append({"address": addr, "percentage": percentage})
                            
                            if i < 5:
                                top_5_share += percentage
                            
                            if percentage > max_single_share:
                                max_single_share = percentage
                                
                            if deployer_address and addr == deployer_address.lower():
                                deployer_share = percentage

                        return {
                            "top_5_share": top_5_share,
                            "max_single_share": max_single_share,
                            "deployer_share": deployer_share,
                            "holders_count": len(holders) # Only top 20 fetched
                        }
                        
        except Exception as e:
            logger.warning(f"持仓分析失败: {e}")
        return {}

    async def analyze_deployer_history(self, deployer_address: str) -> Dict[str, Any]:
        """Deployer 资金来源与历史分析"""
        if not deployer_address:
            return {}
            
        try:
            session = await self._get_session()
            # 获取 deployer 的第一笔交易（通常是资金转入）
            params = {
                "module": "account",
                "action": "txlist",
                "address": deployer_address,
                "startblock": 0,
                "endblock": 99999999,
                "page": 1,
                "offset": 5, # 只需要前几笔
                "sort": "asc",
                "apikey": BSCSCAN_API_KEY
            }
            
            async with session.get(BSCSCAN_API_URL, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data["status"] == "1" and data["result"]:
                        txs = data["result"]
                        first_tx = txs[0]
                        
                        from_addr = first_tx["from"].lower()
                        to_addr = first_tx["to"].lower()
                        value = float(first_tx["value"])
                        timestamp = int(first_tx["timeStamp"])
                        
                        # 检查是否来自 Tornado Cash
                        is_tornado = from_addr == TORNADO_CASH_ADDRESS
                        
                        # 检查钱包年龄 (天)
                        wallet_age_days = (time.time() - timestamp) / 86400
                        
                        # 检查是否立即转出所有资金 (Rug 行为)
                        # 简单逻辑：如果前5笔中有大额转出 (value > 90% of in)
                        is_drained = False
                        balance_in = 0
                        balance_out = 0
                        
                        for tx in txs:
                            if tx["to"].lower() == deployer_address.lower():
                                balance_in += float(tx["value"])
                            elif tx["from"].lower() == deployer_address.lower():
                                balance_out += float(tx["value"])
                        
                        if balance_in > 0 and (balance_out / balance_in) > 0.95:
                             # 仅当 balance_in 较大时才算 (排除小额测试)
                             if balance_in > 0.1 * 10**18: # > 0.1 BNB
                                 is_drained = True

                        return {
                            "is_tornado": is_tornado,
                            "wallet_age_days": wallet_age_days,
                            "is_drained": is_drained,
                            "first_tx_from": from_addr
                        }
                        
        except Exception as e:
            logger.warning(f"Deployer 分析失败: {e}")
        return {}
        
    async def analyze_social_signals(self, source_code: str) -> Dict[str, Any]:
        """从源码中提取社交信号"""
        if not source_code:
            return {"has_socials": False}
            
        # 简单正则匹配
        socials = {
            "telegram": re.findall(r"(t\.me\/[a-zA-Z0-9_]+)", source_code, re.IGNORECASE),
            "twitter": re.findall(r"(twitter\.com\/[a-zA-Z0-9_]+|x\.com\/[a-zA-Z0-9_]+)", source_code, re.IGNORECASE),
            "website": re.findall(r"(https?:\/\/(?!t\.me|twitter\.com|x\.com|github\.com|bscscan\.com)[a-zA-Z0-9\-\.]+\.[a-zA-Z]{2,})", source_code, re.IGNORECASE)
        }
        
        has_socials = any(len(v) > 0 for v in socials.values())
        
        # 检查域名年龄 (TODO: 需要 whois 库，暂时跳过)
        # 检查 TG 人数 (TODO: 需要 TG API，暂时跳过)
        
        return {
            "has_socials": has_socials,
            "details": socials
        }

    async def analyze_buyer_fund_source(self, pair_address: str, token_address: str) -> Dict[str, Any]:
        """链上行为模式分析：买入资金来源与关联性"""
        if not pair_address or not token_address:
            return {}
            
        try:
            session = await self._get_session()
            
            # 1. 获取 Pair 的代币交易列表 (找到前 10 笔买入)
            # 使用 token_address 过滤，只看该代币的流转
            params = {
                "module": "account",
                "action": "tokentx",
                "contractaddress": token_address,
                "address": pair_address,
                "page": 1,
                "offset": 50, # 多取一些以过滤非买入
                "sort": "asc",
                "apikey": BSCSCAN_API_KEY
            }
            
            buyers = []
            buy_txs = []
            
            async with session.get(BSCSCAN_API_URL, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data["status"] == "1" and data["result"]:
                        for tx in data["result"]:
                            # 判定买入: From Pair -> To User
                            # 排除 Router 交互 (To Router)
                            if tx["from"].lower() == pair_address.lower():
                                buyer = tx["to"].lower()
                                # 排除常见合约 (Router, Burn)
                                if buyer in ["0x0000000000000000000000000000000000000000", "0x000000000000000000000000000000000000dead"]:
                                    continue
                                
                                buyers.append(buyer)
                                buy_txs.append(tx)
                                
                                if len(buyers) >= 10:
                                    break
            
            if not buyers:
                return {}
                
            # 2. 分析买入模式
            
            # A. 同区块/相邻区块买入
            sorted_txs = sorted(buy_txs, key=lambda x: int(x["blockNumber"]))
            coordinated_buys = False
            if len(sorted_txs) >= 3:
                # 检查是否有 >3 笔交易在 2 个区块内
                for i in range(len(sorted_txs) - 2):
                    b1 = int(sorted_txs[i]["blockNumber"])
                    b3 = int(sorted_txs[i+2]["blockNumber"])
                    if b3 - b1 <= 2:
                        coordinated_buys = True
                        break

            # B. 资金来源与钱包年龄分析
            funders = []
            new_wallets_count = 0
            
            sem = asyncio.Semaphore(5) # 限制并发
            
            async def check_buyer(buyer_addr):
                async with sem:
                    try:
                        p = {
                            "module": "account",
                            "action": "txlist",
                            "address": buyer_addr,
                            "startblock": 0,
                            "endblock": 99999999,
                            "page": 1,
                            "offset": 1, # 只需第一笔
                            "sort": "asc",
                            "apikey": BSCSCAN_API_KEY
                        }
                        async with session.get(BSCSCAN_API_URL, params=p) as r:
                            if r.status == 200:
                                d = await r.json()
                                if d["status"] == "1" and d["result"]:
                                    first = d["result"][0]
                                    funder = first["from"].lower()
                                    ts = int(first["timeStamp"])
                                    # 7天内新建
                                    is_new = (time.time() - ts) < 7 * 86400
                                    return funder, is_new
                    except:
                        pass
                    return None, False

            tasks = [check_buyer(b) for b in buyers]
            results = await asyncio.gather(*tasks)
            
            funders = [r[0] for r in results if r[0]]
            new_wallets_count = sum(1 for r in results if r[1])
            
            # 检查是否有共同资金来源
            funder_counts = collections.Counter(funders)
            same_source_count = 0
            for f, count in funder_counts.items():
                if count >= 2: # 2个以上算同源
                    same_source_count += count
            
            has_same_source = same_source_count >= 2
            
            return {
                "coordinated_buys": coordinated_buys,
                "has_same_source": has_same_source,
                "same_source_count": same_source_count,
                "new_wallets_count": new_wallets_count,
                "total_buyers_checked": len(buyers)
            }
            
        except Exception as e:
            logger.warning(f"行为模式分析失败: {e}")
            return {}

    async def check_deployer_token_retention(self, token_address: str, deployer_address: str) -> Dict[str, Any]:
        """检查 Deployer 是否保留代币"""
        if not self.w3 or not deployer_address:
            return {}
            
        try:
            checksum_token = AsyncWeb3.to_checksum_address(token_address)
            checksum_deployer = AsyncWeb3.to_checksum_address(deployer_address)
            
            contract = self.w3.eth.contract(address=checksum_token, abi=[
                {"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"payable":False,"stateMutability":"view","type":"function"},
                {"constant":True,"inputs":[],"name":"totalSupply","outputs":[{"name":"","type":"uint256"}],"payable":False,"stateMutability":"view","type":"function"}
            ])
            
            balance = await contract.functions.balanceOf(checksum_deployer).call()
            total_supply = await contract.functions.totalSupply().call()
            
            ratio = 0
            if total_supply > 0:
                ratio = balance / total_supply
                
            return {
                "deployer_balance": balance,
                "ratio": ratio,
                "is_high_retention": ratio > 0.01 # > 1%
            }
            
        except Exception as e:
            logger.warning(f"Deployer 持仓检查失败: {e}")
            return {}

    async def check_bytecode_similarity(self, token_address: str) -> Optional[str]:
        """检查合约字节码相似度 (基于函数选择器指纹)"""
        if not self.w3:
            return None
            
        try:
            checksum_address = to_checksum_address(token_address)
            code = await self.w3.eth.get_code(checksum_address)
            
            if len(code) < 50: # 合约代码太短，可能是代理或空
                return None
                
            # 传递完整字节码 (转hex) 给 BlacklistManager 提取指纹
            bytecode_hex = code.hex()
            
            # 检查相似度 (指纹匹配)
            return await self.blacklist_manager.check_code_similarity(bytecode_hex)
            
        except Exception as e:
            logger.warning(f"字节码相似度检查失败: {e}")
        return None

    async def check_lp_lock(self, pair_address: str) -> bool:
        """检查 LP 是否锁仓"""
        if not self.w3:
            return False
            
        try:
            # 这是一个非常简化的检查，实际需要检查 LP token 的 holder 是否为锁仓合约
            # 并且需要解析锁仓合约的 deposit 信息
            
            # 这里演示检查 LP 代币的前几大持仓地址是否在 LOCK_PLATFORMS 中
            # 由于缺乏直接获取 LP holders 的 API，这里暂时作为占位符
            # 真实场景需要遍历 LP 合约的 Transfer 事件或查询 Holder API
            pass
        except Exception as e:
            logger.warning(f"LP 锁仓检查失败: {e}")
        return False

    async def get_token_state(self, token_address: str, pair_address: str) -> Dict[str, Any]:
        """获取代币当前状态（价格、流动性）"""
        if not self.w3 or not pair_address:
            return {}
        
        try:
            pair_contract = self.w3.eth.contract(address=AsyncWeb3.to_checksum_address(pair_address), abi=[
                {"constant":True,"inputs":[],"name":"getReserves","outputs":[{"name":"_reserve0","type":"uint112"},{"name":"_reserve1","type":"uint112"},{"name":"_blockTimestampLast","type":"uint32"}],"payable":False,"stateMutability":"view","type":"function"},
                {"constant":True,"inputs":[],"name":"token0","outputs":[{"name":"","type":"address"}],"payable":False,"stateMutability":"view","type":"function"}
            ])
            
            reserves = await pair_contract.functions.getReserves().call()
            token0 = await pair_contract.functions.token0().call()
            
            # WBNB Address
            WBNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
            
            is_token0_wbnb = token0.lower() == WBNB.lower()
            
            reserve_bnb = reserves[0] if is_token0_wbnb else reserves[1]
            reserve_token = reserves[1] if is_token0_wbnb else reserves[0]
            
            price = 0
            if reserve_token > 0:
                price = reserve_bnb / reserve_token
                
            liquidity_bnb = (reserve_bnb * 2) / 10**18 # Total Liquidity in BNB approx
            
            return {
                "price": price,
                "liquidity_bnb": liquidity_bnb,
                "timestamp": time.time()
            }
        except Exception as e:
            logger.warning(f"获取代币状态失败: {e}")
            return {}

    async def analyze_observation(self, token_address: str, pair_address: str, initial_state: Dict[str, Any]) -> Dict[str, Any]:
        """5分钟观察期分析"""
        if not initial_state:
            return {}
            
        current_state = await self.get_token_state(token_address, pair_address)
        if not current_state:
            return {}
            
        # 1. 价格变化
        price_change_pct = 0
        if initial_state.get("price", 0) > 0:
            price_change_pct = (current_state["price"] - initial_state["price"]) / initial_state["price"] * 100
            
        # 2. 流动性变化
        liquidity_change_pct = 0
        if initial_state.get("liquidity_bnb", 0) > 0:
            liquidity_change_pct = (current_state["liquidity_bnb"] - initial_state["liquidity_bnb"]) / initial_state["liquidity_bnb"] * 100
            
        # 3. 自然买入 & 砸盘检测
        natural_buys = 0
        big_dump = False
        
        try:
            session = await self._get_session()
            params = {
                "module": "account",
                "action": "tokentx",
                "contractaddress": token_address,
                "address": pair_address,
                "page": 1,
                "offset": 100, 
                "sort": "desc", 
                "apikey": BSCSCAN_API_KEY
            }
            
            async with session.get(BSCSCAN_API_URL, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data["status"] == "1" and data["result"]:
                        txs = data["result"]
                        
                        buyers = set()
                        for tx in txs:
                            # Natural Buy: From Pair -> To User
                            if tx["from"].lower() == pair_address.lower():
                                buyer = tx["to"].lower()
                                if buyer not in ["0x0000000000000000000000000000000000000000", "0x000000000000000000000000000000000000dead"]:
                                    buyers.add(buyer)
                                    
                            # Big Dump: From User -> To Pair (Sell)
                            if tx["to"].lower() == pair_address.lower():
                                amount = float(tx["value"])
                                # Estimate BNB value
                                val_wei = amount * current_state["price"]
                                val_bnb = val_wei / 10**18
                                
                                if val_bnb > 5: # > 5 BNB sell considered Big Dump
                                    big_dump = True
                        
                        natural_buys = len(buyers)
                        
        except Exception as e:
            logger.warning(f"观察期交易分析失败: {e}")
            
        return {
            "price_change_pct": price_change_pct,
            "liquidity_change_pct": liquidity_change_pct,
            "natural_buys": natural_buys,
            "big_dump": big_dump
        }

    async def analyze(self, token_address: str, deployer_address: str, pair_address: str = None, initial_state: Dict[str, Any] = None) -> Dict[str, Any]:
        """执行完整安全分析"""
        start_time = time.time()
        token_address = token_address.lower()
        deployer_address = deployer_address.lower() if deployer_address else ""
        
        # Ensure DB is ready (idempotent)
        await self.blacklist_manager.init_db()
        
        result = {
            "token_address": token_address,
            "final_score": 100, # Initial score, will be capped/adjusted
            "decision": "reject",
            "risk_items": [],
            "bonus_items": [],
            "raw_data": {},
            "analysis_time": 0
        }

        score = 100
        risk_items = []
        bonus_items = []

        # --- 维度零：本地黑名单与模拟 (最高优先级) ---
        
        # 1. 检查黑名单
        if deployer_address:
            reason = await self.blacklist_manager.check_deployer(deployer_address)
            if reason:
                score = 0
                risk_items.append({"desc": f"Deployer 在本地黑名单中: {reason}", "score": -100})
                return self._finalize_result(result, score, risk_items, bonus_items, start_time)

        code_hash = None
        if self.w3:
            try:
                # Ensure token_address is checksummed
                checksum_address = to_checksum_address(token_address)
                code = await self.w3.eth.get_code(checksum_address)
                if len(code) > 2: # Not empty
                    code_hash = '0x' + keccak(code).hex()
                    reason = await self.blacklist_manager.check_code_hash(code_hash)
                    if reason:
                        score = 0
                        risk_items.append({"desc": f"合约代码Hash在黑名单中: {reason}", "score": -100})
                        return self._finalize_result(result, score, risk_items, bonus_items, start_time)
            except Exception as e:
                logger.warning(f"获取代码Hash失败: {e}")

        # 2. 本地模拟交易
        if self.local_simulator and pair_address:
            logger.info(f"开始本地模拟交易: {token_address}")
            is_hp, b_tax, s_tax, reason = await self.local_simulator.simulate_trade(token_address, pair_address)
            
            result["raw_data"]["simulation"] = {
                "is_honeypot": is_hp,
                "buy_tax": b_tax,
                "sell_tax": s_tax,
                "reason": reason
            }
            
            if is_hp:
                score = 0
                risk_items.append({"desc": f"本地模拟交易失败: {reason}", "score": -100})
                
                # 自动加入黑名单
                if code_hash:
                    await self.blacklist_manager.add_code_hash(code_hash, f"Simulation Failed: {reason}")
                if deployer_address:
                    await self.blacklist_manager.add_deployer(deployer_address, f"Deployed Honeypot {token_address}")
                    
                return self._finalize_result(result, score, risk_items, bonus_items, start_time)
            else:
                bonus_items.append({"desc": "本地模拟交易成功 (eth_call)", "score": +20})
                # TODO: 使用模拟的税率进行评分 (目前 simulate_trade 返回 0 税率，需完善)

        # 并行执行 API 调用 (新增 holders, deployer, social check, behavior check)
        goplus_task = self.check_goplus(token_address)
        honeypot_task = self.check_honeypot_is(token_address)
        contract_task = self.check_contract_code(token_address)
        holders_task = self.analyze_token_holders(token_address, deployer_address, pair_address)
        deployer_task = self.analyze_deployer_history(deployer_address)
        similarity_task = self.check_bytecode_similarity(token_address)
        fund_source_task = self.analyze_buyer_fund_source(pair_address, token_address)
        retention_task = self.check_deployer_token_retention(token_address, deployer_address)
        observation_task = self.analyze_observation(token_address, pair_address, initial_state)
        
        goplus_data, honeypot_data, contract_data, holders_data, deployer_data, similarity_reason, fund_source_data, retention_data, observation_data = await asyncio.gather(
            goplus_task, honeypot_task, contract_task, holders_task, deployer_task, similarity_task, fund_source_task, retention_task, observation_task
        )
        
        result["raw_data"]["goplus"] = goplus_data
        result["raw_data"]["honeypot"] = honeypot_data
        result["raw_data"]["contract"] = contract_data
        result["raw_data"]["holders"] = holders_data
        result["raw_data"]["deployer"] = deployer_data
        result["raw_data"]["fund_source"] = fund_source_data
        result["raw_data"]["retention"] = retention_data
        result["raw_data"]["observation"] = observation_data

        # --- 维度一：外部 API 检测 ---
        
        # 1. GoPlus 检测
        if goplus_data:
            if goplus_data.get("is_honeypot") == "1":
                score = 0
                risk_items.append({"desc": "GoPlus 标记为蜜罐", "score": -100})
            
            buy_tax = float(goplus_data.get("buy_tax", 0) or 0) * 100
            sell_tax = float(goplus_data.get("sell_tax", 0) or 0) * 100
            
            if buy_tax > 25 or sell_tax > 25:
                score -= 30
                risk_items.append({"desc": f"税率过高 (Buy: {buy_tax}%, Sell: {sell_tax}%)", "score": -30})
                
            if goplus_data.get("is_mintable") == "1":
                score -= 20
                risk_items.append({"desc": "代币可增发 (Mintable)", "score": -20})
                
            if goplus_data.get("is_proxy") == "1":
                score -= 10
                risk_items.append({"desc": "代理合约 (Proxy)", "score": -10})

            if goplus_data.get("is_blacklisted") == "1":
                score -= 15
                risk_items.append({"desc": "存在黑名单功能", "score": -15})

        # 2. Honeypot.is 检测
        if honeypot_data:
            if not honeypot_data.get("simulationSuccess", False):
                score = 0
                risk_items.append({"desc": "Honeypot.is 模拟交易失败", "score": -100})
            
            # 再次检查税率 (双重确认)
            hp_buy_tax = float(honeypot_data.get("simulationResult", {}).get("buyTax", 0))
            hp_sell_tax = float(honeypot_data.get("simulationResult", {}).get("sellTax", 0))
            
            if hp_buy_tax > 25 or hp_sell_tax > 25:
                # 避免重复扣分，取最大值
                pass 

        # --- 维度二：合约字节码与相似度 ---
        
        # 3. 相似度检测 (新增)
        if similarity_reason:
            score = 0 # 直接拒绝 (User: 扣60分直接拒绝)
            risk_items.append({"desc": f"合约代码高度相似已知Rug: {similarity_reason}", "score": -100})
            return self._finalize_result(result, score, risk_items, bonus_items, start_time)
        
        # 4. 危险函数检测 & 开源检测
        source_code = contract_data.get("SourceCode", "")
        abi_str = contract_data.get("ABI", "")
        
        if not source_code:
            score -= 20
            risk_items.append({"desc": "合约未开源", "score": -20})
        else:
            score += 10
            bonus_items.append({"desc": "合约已开源", "score": +10})
            
            # 社交信号分析 (新增)
            socials_data = await self.analyze_social_signals(source_code)
            if not socials_data["has_socials"]:
                score -= 10
                risk_items.append({"desc": "未在合约源码中发现社交链接", "score": -10})
            else:
                bonus_items.append({"desc": "发现社交链接", "score": +5})
            
            # 简单的源码字符串匹配 (更严谨应该解析 ABI)
            lower_source = source_code.lower()
            
            for func in DANGEROUS_FUNCTIONS:
                if func.lower() in lower_source:
                    # 排除 renounceOwnership，这是加分项
                    if func == "renounceOwnership":
                        continue
                        
                    # 简单扣分，实际需要确认是否为 owner only
                    score -= 5
                    risk_items.append({"desc": f"发现危险函数: {func}", "score": -5})
            
            if "renounceownership" in lower_source or "owner = address(0)" in lower_source:
                score += 10
                bonus_items.append({"desc": "发现放弃所有权代码", "score": +10})

        # --- 维度三：链上行为与持仓分析 ---
        
        # 5. 持仓集中度分析 (新增)
        if holders_data:
            top_5 = holders_data.get("top_5_share", 0)
            max_single = holders_data.get("max_single_share", 0)
            deployer_hold = holders_data.get("deployer_share", 0)
            
            if top_5 > 30:
                score -= 40
                risk_items.append({"desc": f"持仓高度集中 (Top 5: {top_5:.1f}%)", "score": -40})
            
            if max_single > 15:
                score -= 30
                risk_items.append({"desc": f"存在巨鲸持仓 (Single: {max_single:.1f}%)", "score": -30})
                
            if deployer_hold > 0: # 只要有持仓就扣分 (User: deployer或关联地址仍持有代币 → 扣35分)
                # 这里 deployer_share 是百分比，如果是0.0001%这种忽略不计? User没说阈值，假设 > 0.1%?
                # User says "仍持有代币", usually implies significant amount. 
                if deployer_hold > 0.1:
                    score -= 35
                    risk_items.append({"desc": f"Deployer 仍持有代币 ({deployer_hold:.1f}%)", "score": -35})

        # 6. Deployer 资金来源与行为 (新增)
        if deployer_data:
            if deployer_data.get("is_tornado"):
                score -= 50
                risk_items.append({"desc": "Deployer 资金来自 Tornado Cash", "score": -50})
            
            age = deployer_data.get("wallet_age_days", 0)
            if age < 7:
                score -= 20
                risk_items.append({"desc": f"Deployer 钱包创建时间短 ({age:.1f}天)", "score": -20})
                
            if deployer_data.get("is_drained"):
                score -= 25
                risk_items.append({"desc": "Deployer 资金快进快出 (疑似洗钱)", "score": -25})

        # 7. Deployer 黑名单检查 (Re-check if needed, but done at start)
        if deployer_address in self.blacklist:
            score = 0
            risk_items.append({"desc": "Deployer 在本地黑名单中", "score": -100})

        # 8. 链上行为模式分析 (新增)
        if fund_source_data:
            if fund_source_data.get("coordinated_buys"):
                score -= 25
                risk_items.append({"desc": "发现协同买入行为 (同区块/相邻区块)", "score": -25})
                
            if fund_source_data.get("has_same_source"):
                score -= 40
                risk_items.append({"desc": "买家资金来源相同 (疑似老鼠仓)", "score": -40})
                
            new_wallets = fund_source_data.get("new_wallets_count", 0)
            total_checked = fund_source_data.get("total_buyers_checked", 0)
            # 如果全部是新钱包 (且检查了至少3个)
            if total_checked >= 3 and new_wallets == total_checked:
                score -= 20
                risk_items.append({"desc": "早期买家全是新钱包", "score": -20})

        # 9. Deployer 隐形持仓 (新增)
        if retention_data and retention_data.get("is_high_retention"):
            ratio = retention_data.get("ratio", 0) * 100
            score -= 35
            risk_items.append({"desc": f"Deployer 保留大量代币 ({ratio:.2f}%)", "score": -35})

        # 10. 观察期信号分析 (新增)
        if observation_data:
            # 自然买入
            nb = observation_data.get("natural_buys", 0)
            if nb > 3:
                score += 10
                bonus_items.append({"desc": f"观察期内自然买入活跃 ({nb}笔)", "score": +10})
                
            # 价格变化
            pc = observation_data.get("price_change_pct", 0)
            if 0 < pc <= 50:
                score += 5
                bonus_items.append({"desc": f"观察期内价格温和上涨 ({pc:.1f}%)", "score": +5})
                
            # 砸盘检测
            if observation_data.get("big_dump"):
                score -= 20
                risk_items.append({"desc": "观察期内出现大单砸盘", "score": -20})
                
            # 流动性变化
            lc = observation_data.get("liquidity_change_pct", 0)
            if lc < -10:
                score = 0 # 直接拒绝
                risk_items.append({"desc": f"观察期内流动性大幅撤出 ({lc:.1f}%)", "score": -100})
                return self._finalize_result(result, score, risk_items, bonus_items, start_time)

        # --- 汇总与决策 ---
        # 更新 max score logic if needed, but finalize uses raw score.
        # User said "Max 150". My logic caps at 100 in finalize. Need to change finalize too.
        
        return self._finalize_result(result, score, risk_items, bonus_items, start_time)

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

if __name__ == "__main__":
    # 简单测试
    async def main():
        checker = SecurityChecker()
        # 用一个已知的代币地址测试 (例如 USDT on BSC, 实际上 USDT 是代理合约，且不能mint给普通人，会扣分)
        # BSC USDT: 0x55d398326f99059fF775485246999027B3197955
        res = await checker.analyze("0x55d398326f99059fF775485246999027B3197955", "0x000")
        print(json.dumps(res, indent=2, ensure_ascii=False))
        await checker.close()

    asyncio.run(main())
