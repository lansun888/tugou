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
from bsc_bot.utils.multicall_helper import multicall3_batch

# 加载环境变量
load_dotenv()


class SmartCache:
    """轻量级 TTL 缓存，线程/协程安全（单进程异步场景）。"""

    def __init__(self):
        self._store: dict = {}

    def get(self, key: str, max_age_seconds: float = 60):
        entry = self._store.get(key)
        if entry is not None:
            value, ts = entry
            if time.time() - ts < max_age_seconds:
                return value
        return None

    def set(self, key: str, value) -> None:
        self._store[key] = (value, time.time())

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)


# 模块级单例，跨同一进程所有 SecurityChecker 实例共享
_cache = SmartCache()

# 常量定义
CHAIN_ID = 56
GOPLUS_API_URL = "https://api.gopluslabs.io/api/v1/token_security/56"
HONEYPOT_IS_API_URL = "https://api.honeypot.is/v2/IsHoneypot"
# 升级到 BSCScan V2 API (使用 Etherscan V2 统一端点)
BSCSCAN_API_URL = "https://api.etherscan.io/v2/api?chainid=56" 
BSCSCAN_API_KEY = os.getenv("BSCSCAN_API_KEY", "")

# GMGN API
GMGN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://gmgn.ai/",
}

# 必须成功的检测项 (Fail-Safe)
# holders 不在此列：新币上线初期无真实钱包持仓是正常现象，改为评分降权而非硬拒绝
REQUIRED_CHECKS = [
    "goplus",
    "honeypot",
    "contract", # 源码获取
]

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
        self.db_path = None
        self.local_simulator = LocalSimulator(w3) if w3 else None
        self.blacklist_manager = BlacklistManager()
        self.blacklist = self._load_blacklist()

    def set_web3(self, w3: AsyncWeb3):
        """Update Web3 instance and re-initialize simulator"""
        self.w3 = w3

    def set_db_path(self, db_path: str):
        """设置本地数据库路径（用于 deployer 历史查询）"""
        self.db_path = db_path

    async def _check_deployer_history(self, deployer_address: str) -> Dict[str, Any]:
        """查询 deployer 历史发币的 rug 率（本地DB + BSCScan双源）"""
        if not deployer_address:
            return {"passed": True, "total": 0, "rugs": 0, "onchain_total": 0, "onchain_migrated": 0}

        FOUR_MEME_FACTORY = "0x5c952063c7fc8610ffdb798152d69f0b9550762b"

        # ── 本地DB查询 & BSCScan并行 ──
        local_total, local_rugs = 0, 0
        onchain_total, onchain_migrated = 0, 0

        async def _local_db_query():
            if not self.db_path:
                return 0, 0
            try:
                import aiosqlite
                async with aiosqlite.connect(self.db_path) as db:
                    async with db.execute(
                        """
                        SELECT COUNT(*) as total,
                          SUM(CASE WHEN close_reason IN (
                            'rug_pull_confirmed','liq_drain_2min','delayed_honeypot',
                            'liq_drop_2min','no_activity_2min'
                          ) THEN 1 ELSE 0 END) as rugs
                        FROM simulation_positions
                        WHERE deployer_address = ?
                        """,
                        (deployer_address,)
                    ) as cursor:
                        row = await cursor.fetchone()
                        return (row[0] or 0, row[1] or 0) if row else (0, 0)
            except Exception as e:
                logger.debug(f"[deployer] 本地DB查询失败: {e}")
                return 0, 0

        async def _bscscan_query():
            """BSCScan双查询：txlist(factory调用)=发币数 + txlistinternal(factory→deployer)=迁移数"""
            try:
                session = await self._get_session()
                # 查询1：deployer → factory 的txns = 总发币数
                params_tx = {
                    "module": "account", "action": "txlist",
                    "address": deployer_address,
                    "startblock": 0, "endblock": 99999999,
                    "page": 1, "offset": 100, "sort": "desc",
                    "apikey": BSCSCAN_API_KEY
                }
                # 查询2：factory → deployer 的内部txns = 迁移数（graduation时工厂退费）
                params_int = {
                    "module": "account", "action": "txlistinternal",
                    "address": deployer_address,
                    "startblock": 0, "endblock": 99999999,
                    "page": 1, "offset": 100, "sort": "desc",
                    "apikey": BSCSCAN_API_KEY
                }
                async def _get(params):
                    async with session.get(BSCSCAN_API_URL, params=params, timeout=5) as r:
                        return await r.json()
                d1, d2 = await asyncio.gather(_get(params_tx), _get(params_int))

                txns = d1.get("result", []) if isinstance(d1.get("result"), list) else []
                internal = d2.get("result", []) if isinstance(d2.get("result"), list) else []

                total = sum(1 for t in txns
                            if t.get("to", "").lower() == FOUR_MEME_FACTORY
                            and t.get("isError") == "0")
                migrated = sum(1 for t in internal
                               if t.get("from", "").lower() == FOUR_MEME_FACTORY
                               and t.get("isError") == "0"
                               and int(t.get("value", 0)) > 0)
                return total, migrated
            except Exception as e:
                logger.debug(f"[deployer] BSCScan查询失败(跳过): {e}")
                return 0, 0

        (local_total, local_rugs), (onchain_total, onchain_migrated) = await asyncio.gather(
            _local_db_query(), _bscscan_query()
        )

        result = {
            "passed": True,
            "total": local_total, "rugs": local_rugs,
            "onchain_total": onchain_total, "onchain_migrated": onchain_migrated,
        }

        # ── 本地DB rug率判断（已有数据）──
        if local_total >= 3 and local_rugs / local_total > 0.5:
            result["passed"] = False
            result["reason"] = f"本地历史rug率{local_rugs/local_total*100:.0f}%({local_rugs}/{local_total})"
            return result

        # ── 链上发币数判断（BSCScan） ──
        if onchain_total >= 5:
            migration_rate = onchain_migrated / onchain_total
            if onchain_migrated == 0:
                # 发过>=5个币但无一迁移（全部归零）
                result["passed"] = False
                result["reason"] = f"Dev发过{onchain_total}个币迁移率0%（全部归零）"
                return result
            if onchain_total >= 10 and migration_rate < 0.1:
                result["passed"] = False
                result["reason"] = (f"Dev成功率仅{migration_rate*100:.0f}%"
                                    f"({onchain_migrated}/{onchain_total})")
                return result

        return result
        self.local_simulator = LocalSimulator(w3) if w3 else None
        
    async def _get_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=8),   # 各子调用自带 5s，8s 作兜底
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
        cache_key = f"goplus:{token_address.lower()}"
        cached = _cache.get(cache_key, max_age_seconds=300)  # 5 min
        if cached is not None:
            logger.debug(f"[cache] goplus hit: {token_address[:10]}…")
            return cached

        try:
            session = await self._get_session()
            params = {"contract_addresses": token_address}
            # Short timeout for GoPlus
            async with session.get(GOPLUS_API_URL, params=params, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # Ensure we extract data for THIS token address only
                    result = data.get("result", {}).get(token_address.lower(), {})

                    # Log raw data for verification
                    logger.info(f"GoPlus Raw Data for {token_address}: {json.dumps(result)}")

                    if not result:
                         # Try original case if lower failed (API quirk?)
                         result = data.get("result", {}).get(token_address, {})
                    if result:
                        _cache.set(cache_key, result)
                    return result
        except Exception as e:
            logger.warning(f"GoPlus API 检测失败: {e}")
        return {}

    async def check_honeypot_is(self, token_address: str) -> Dict[str, Any]:
        """Honeypot.is API 检测"""
        cache_key = f"honeypot:{token_address.lower()}"
        cached = _cache.get(cache_key, max_age_seconds=300)  # 5 min
        if cached is not None:
            logger.debug(f"[cache] honeypot hit: {token_address[:10]}…")
            return cached

        try:
            session = await self._get_session()
            params = {"address": token_address, "chainID": CHAIN_ID}
            # Short timeout
            async with session.get(HONEYPOT_IS_API_URL, params=params, timeout=5) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if result:
                        _cache.set(cache_key, result)
                    return result
        except Exception as e:
            logger.warning(f"Honeypot.is API 检测失败: {e}")
        return {}

    async def check_contract_code(self, token_address: str) -> Dict[str, Any]:
        """合约代码分析 (通过 BscScan 获取 ABI/Source)"""
        cache_key = f"contract:{token_address.lower()}"
        cached = _cache.get(cache_key, max_age_seconds=1800)  # 30 min — 源码不可变
        if cached is not None:
            logger.debug(f"[cache] contract hit: {token_address[:10]}…")
            return cached

        try:
            session = await self._get_session()
            params = {
                "module": "contract",
                "action": "getsourcecode",
                "address": token_address,
                "apikey": BSCSCAN_API_KEY
            }
            # BscScan might be slow, but 5s is usually enough for API
            async with session.get(BSCSCAN_API_URL, params=params, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data["status"] == "1" and data["result"]:
                        result = data["result"][0]
                        _cache.set(cache_key, result)
                        return result
        except Exception as e:
            logger.warning(f"合约代码获取失败: {e}")
        return {}
    
    async def analyze_token_holders(self, token_address: str, deployer_address: str = None, pair_address: str = None) -> Dict[str, Any]:
        """代币持仓分析 (使用 BscScan API)"""
        try:
            session = await self._get_session()
            params = {
                "module": "token",
                "action": "tokenholderlist",
                "contractaddress": token_address,
                "page": 1,
                "offset": 20,
                "apikey": BSCSCAN_API_KEY
            }
            
            # Short timeout for holders check
            async with session.get(BSCSCAN_API_URL, params=params, timeout=5) as resp:
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
                                # multicall: totalSupply + decimals → 1 RPC
                                mc_res = await multicall3_batch(self.w3, [
                                    (token_address, "totalSupply()", [], [], ["uint256"]),
                                    (token_address, "decimals()",    [], [], ["uint8"]),
                                ])
                                raw_supply, decimals = mc_res[0], mc_res[1]
                                if raw_supply is not None and decimals is not None:
                                    real_total_supply = raw_supply / (10 ** decimals)
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
            import traceback
            logger.warning(f"Traceback: {traceback.format_exc()}")
        return {}

    async def analyze_deployer_history(self, deployer_address: str) -> Dict[str, Any]:
        """Deployer 资金来源与历史分析"""
        if not deployer_address:
            return {}

        cache_key = f"deployer:{deployer_address.lower()}"
        cached = _cache.get(cache_key, max_age_seconds=600)  # 10 min — 历史交易稳定
        if cached is not None:
            logger.debug(f"[cache] deployer hit: {deployer_address[:10]}…")
            return cached
            
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
            
            async with session.get(BSCSCAN_API_URL, params=params, timeout=5) as resp:
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

                        result = {
                            "is_tornado": is_tornado,
                            "wallet_age_days": wallet_age_days,
                            "is_drained": is_drained,
                            "first_tx_from": from_addr
                        }
                        _cache.set(cache_key, result)
                        return result

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

        cache_key = f"buyers:{pair_address.lower()}:{token_address.lower()}"
        cached = _cache.get(cache_key, max_age_seconds=180)  # 3 min — 新买入会陆续进来
        if cached is not None:
            logger.debug(f"[cache] buyers hit: {token_address[:10]}…")
            return cached
            
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
            
            async with session.get(BSCSCAN_API_URL, params=params, timeout=5) as resp:
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
                        async with session.get(BSCSCAN_API_URL, params=p, timeout=3) as r:
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

            result = {
                "coordinated_buys": coordinated_buys,
                "has_same_source": has_same_source,
                "same_source_count": same_source_count,
                "new_wallets_count": new_wallets_count,
                "total_buyers_checked": len(buyers)
            }
            _cache.set(cache_key, result)
            return result

        except Exception as e:
            logger.warning(f"行为模式分析失败: {e}")
            return {}

    async def check_deployer_token_retention(self, token_address: str, deployer_address: str) -> Dict[str, Any]:
        """检查 Deployer 是否保留代币"""
        if not self.w3 or not deployer_address:
            return {}

        cache_key = f"retention:{token_address.lower()}:{deployer_address.lower()}"
        cached = _cache.get(cache_key, max_age_seconds=120)  # 2 min — deployer 可能卖出
        if cached is not None:
            logger.debug(f"[cache] retention hit: {token_address[:10]}…")
            return cached
            
        try:
            checksum_token = AsyncWeb3.to_checksum_address(token_address)
            checksum_deployer = AsyncWeb3.to_checksum_address(deployer_address)
            
            contract = self.w3.eth.contract(address=checksum_token, abi=[
                {"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"payable":False,"stateMutability":"view","type":"function"},
                {"constant":True,"inputs":[],"name":"totalSupply","outputs":[{"name":"","type":"uint256"}],"payable":False,"stateMutability":"view","type":"function"}
            ])
            
            # multicall: balanceOf + totalSupply → 1 RPC
            mc_res = await multicall3_batch(self.w3, [
                (checksum_token, "balanceOf(address)", [checksum_deployer], ["address"], ["uint256"]),
                (checksum_token, "totalSupply()",      [],                  [],          ["uint256"]),
            ])
            balance, total_supply = mc_res[0] or 0, mc_res[1] or 0
            
            ratio = 0
            if total_supply > 0:
                ratio = balance / total_supply
                
            result = {
                "deployer_balance": balance,
                "ratio": ratio,
                "is_high_retention": ratio > 0.01  # > 1%
            }
            _cache.set(cache_key, result)
            return result

        except Exception as e:
            logger.warning(f"Deployer 持仓检查失败: {e}")
            return {}

    async def check_bytecode_similarity(self, token_address: str) -> Optional[str]:
        """检查合约字节码相似度 (基于函数选择器指纹)"""
        if not self.w3:
            return None

        cache_key = f"bytecode:{token_address.lower()}"
        cached = _cache.get(cache_key, max_age_seconds=3600)  # 60 min — 字节码不可变
        if cached is not None:
            logger.debug(f"[cache] bytecode hit: {token_address[:10]}…")
            return cached if cached != "__none__" else None

        try:
            checksum_address = to_checksum_address(token_address)
            code = await self.w3.eth.get_code(checksum_address)

            if len(code) < 50:  # 合约代码太短，可能是代理或空
                _cache.set(cache_key, "__none__")
                return None

            # 传递完整字节码 (转hex) 给 BlacklistManager 提取指纹
            bytecode_hex = code.hex()

            # 检查相似度 (指纹匹配)
            result = await self.blacklist_manager.check_code_similarity(bytecode_hex)
            _cache.set(cache_key, result if result is not None else "__none__")
            return result

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
            
            # multicall: getReserves + token0 → 1 RPC
            mc_res = await multicall3_batch(self.w3, [
                (pair_address, "getReserves()", [], [], ["uint112", "uint112", "uint32"]),
                (pair_address, "token0()",      [], [], ["address"]),
            ])
            reserves, token0 = mc_res[0], mc_res[1]

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

        # web3.py 默认 30s 超时，必须显式限制避免拖慢整体安全分析
        try:
            current_state = await asyncio.wait_for(
                self.get_token_state(token_address, pair_address),
                timeout=3.0
            )
        except asyncio.TimeoutError:
            logger.warning("[observation] get_token_state 超时(>3s)，跳过")
            return {}
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
            
            async with session.get(BSCSCAN_API_URL, params=params, timeout=5) as resp:
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

    async def _task_local_checks(self, token_address: str, deployer_address: str, pair_address: str) -> Dict[str, Any]:
        """本地检测（黑名单+代码Hash+模拟交易）打包为单一任务，与外部API并行执行"""
        t0 = time.perf_counter()
        out = {"reject": False, "reason": None, "code_hash": None, "simulation": {}, "sim_bonus": False}
        try:
            # 1. DB 黑名单：deployer
            t1 = time.perf_counter()
            if deployer_address:
                reason = await self.blacklist_manager.check_deployer(deployer_address)
                dur1 = (time.perf_counter() - t1) * 1000
                if reason:
                    out["reject"] = True
                    out["reason"] = f"Deployer 在本地黑名单中: {reason}"
                    logger.info(f"⏱️ [local_checks] step1_deployer_db={dur1:.0f}ms → 命中黑名单，总={( time.perf_counter()-t0)*1000:.0f}ms")
                    return out
            else:
                dur1 = (time.perf_counter() - t1) * 1000
            # 2. 内存黑名单
            t2 = time.perf_counter()
            if deployer_address and deployer_address in self.blacklist:
                out["reject"] = True
                out["reason"] = "Deployer 在本地内存黑名单中"
                dur2 = (time.perf_counter() - t2) * 1000
                logger.info(f"⏱️ [local_checks] step1={dur1:.0f}ms step2_mem={dur2:.0f}ms → 内存黑名单，总={(time.perf_counter()-t0)*1000:.0f}ms")
                return out
            dur2 = (time.perf_counter() - t2) * 1000
            # 3. 代码 Hash + 本地模拟（需要先 get_code）
            t3 = time.perf_counter()
            code_hash = None
            if self.w3:
                try:
                    code = await self.w3.eth.get_code(to_checksum_address(token_address))
                    if len(code) > 2:
                        code_hash = '0x' + keccak(code).hex()
                        out["code_hash"] = code_hash
                        hash_reason = await self.blacklist_manager.check_code_hash(code_hash)
                        if hash_reason:
                            out["reject"] = True
                            out["reason"] = f"合约代码Hash在黑名单中: {hash_reason}"
                            dur3 = (time.perf_counter() - t3) * 1000
                            logger.info(f"⏱️ [local_checks] step1={dur1:.0f}ms step2={dur2:.0f}ms step3_code={dur3:.0f}ms → hash黑名单，总={(time.perf_counter()-t0)*1000:.0f}ms")
                            return out
                except Exception as e:
                    logger.warning(f"代码Hash获取失败: {e}")
            dur3 = (time.perf_counter() - t3) * 1000
            # 4. 本地模拟
            t4 = time.perf_counter()
            if self.local_simulator and pair_address:
                is_hp, b_tax, s_tax, sim_reason = await self.local_simulator.simulate_trade(token_address, pair_address)
                dur4 = (time.perf_counter() - t4) * 1000
                out["simulation"] = {"is_honeypot": is_hp, "buy_tax": b_tax, "sell_tax": s_tax, "reason": sim_reason}
                if sim_reason == "slot_not_found":
                    # slot未找到不等于貔貅，降级到主流程的honeypot.is检测，不拒绝不加黑名单
                    logger.warning(f"[local_checks] slot未找到，跳过simulate判定: {token_address[:10]}")
                elif is_hp:
                    out["reject"] = True
                    out["reason"] = f"本地模拟交易失败: {sim_reason}"
                    if code_hash:
                        await self.blacklist_manager.add_code_hash(code_hash, f"Simulation Failed: {sim_reason}")
                    if deployer_address:
                        await self.blacklist_manager.add_deployer(deployer_address, f"Deployed Honeypot {token_address}")
                else:
                    out["sim_bonus"] = True
            else:
                dur4 = (time.perf_counter() - t4) * 1000
        except Exception as e:
            logger.warning(f"本地检测任务异常: {e}")
            dur1 = dur2 = dur3 = dur4 = -1
        logger.info(f"⏱️ [local_checks] step1_deployer_db={dur1:.0f}ms step2_mem={dur2:.0f}ms step3_get_code={dur3:.0f}ms step4_simulate={dur4:.0f}ms 总={(time.perf_counter()-t0)*1000:.0f}ms")
        return out

    async def _check_gmgn_signals(self, token_address: str) -> dict:
        """GMGN三端点 + DexScreener并行查询（非阻塞，任何端点失败不影响整体）

        token_stat        → holder_count, bot_degen_count, creator_created_count,
                            creator_hold_rate, top_10_holder_rate
        mutil_window      → is_show_alert, flags, buy_tax, sell_tax, launchpad_progress
        DexScreener pairs → socials (twitter, telegram, discord, website)
        """
        addr = token_address.lower()
        result = {
            # 持有人
            "holder_count": None,
            # DS警告
            "is_show_alert": False,
            "flags": [],
            # 税率（来自 mutil_window，补充 honeypot.is）
            "buy_tax": None,
            "sell_tax": None,
            # Launchpad
            "launchpad_progress": None,
            "launchpad_status": None,
            # 持仓分布
            "top_10_holder_rate": None,
            # Dev信息
            "creator_created_count": None,
            "creator_hold_rate": None,
            # Bot Degen（专业机器人认可度）
            "bot_degen_count": None,
            # 社交媒体
            "has_twitter": False,
            "has_telegram": False,
            "has_discord": False,
            "has_website": False,
        }

        try:
            session = await self._get_session()

            async def _fetch_token_stat():
                url = f"https://gmgn.ai/api/v1/token_stat/bsc/{addr}"
                try:
                    async with session.get(url, headers=GMGN_HEADERS,
                                           timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            logger.debug(f"[gmgn_raw] token_stat {addr[:10]} code={data.get('code')} keys={list((data.get('data') or {}).keys())[:10]} raw={str(data)[:300]}")
                            if data.get("code") == 0:
                                return data.get("data") or {}
                        else:
                            logger.debug(f"[gmgn_raw] token_stat {addr[:10]} http={resp.status}")
                except Exception as e:
                    logger.debug(f"[gmgn] token_stat 失败: {e}")
                return {}

            async def _fetch_mutil_window():
                url = f"https://gmgn.ai/api/v1/mutil_window_token_security_launchpad/bsc/{addr}"
                try:
                    async with session.get(url, headers=GMGN_HEADERS,
                                           timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data.get("code") == 0:
                                return data.get("data") or {}
                except Exception as e:
                    logger.debug(f"[gmgn] mutil_window 失败: {e}")
                return {}

            async def _fetch_dexscreener_socials():
                url = f"https://api.dexscreener.com/tokens/v1/bsc/{addr}"
                try:
                    async with session.get(url, headers={"User-Agent": "Mozilla/5.0"},
                                           timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            pairs = data if isinstance(data, list) else data.get("pairs", [])
                            if pairs:
                                return pairs[0].get("info") or {}
                except Exception as e:
                    logger.debug(f"[dexscreener] socials 失败: {e}")
                return {}

            stat, mutil, dex_info = await asyncio.gather(
                _fetch_token_stat(), _fetch_mutil_window(), _fetch_dexscreener_socials(),
                return_exceptions=True
            )

            # ── 解析 token_stat ──
            if isinstance(stat, dict) and stat:
                result["holder_count"] = stat.get("holder_count")
                result["bot_degen_count"] = stat.get("bot_degen_count")
                result["creator_created_count"] = stat.get("creator_created_count")
                try:
                    result["creator_hold_rate"] = float(stat.get("creator_hold_rate") or 0)
                except (ValueError, TypeError):
                    pass
                try:
                    rate = stat.get("top_10_holder_rate")
                    result["top_10_holder_rate"] = float(rate) if rate is not None else None
                except (ValueError, TypeError):
                    pass

            # ── 解析 mutil_window ──
            if isinstance(mutil, dict) and mutil:
                sec = mutil.get("security") or {}
                lp = mutil.get("launchpad") or {}
                result["is_show_alert"] = bool(sec.get("is_show_alert", False))
                result["flags"] = sec.get("flags") or []
                try:
                    result["buy_tax"] = float(sec.get("buy_tax") or 0)
                    result["sell_tax"] = float(sec.get("sell_tax") or 0)
                except (ValueError, TypeError):
                    pass
                try:
                    result["launchpad_progress"] = float(lp.get("launchpad_progress") or 0)
                    result["launchpad_status"] = lp.get("launchpad_status")
                except (ValueError, TypeError):
                    pass

            # ── 解析 DexScreener socials ──
            if isinstance(dex_info, dict) and dex_info:
                for s in (dex_info.get("socials") or []):
                    stype = (s.get("type") or "").lower()
                    if stype == "twitter":
                        result["has_twitter"] = True
                    elif stype == "telegram":
                        result["has_telegram"] = True
                    elif stype == "discord":
                        result["has_discord"] = True
                result["has_website"] = bool(dex_info.get("websites"))

        except Exception as e:
            logger.warning(f"[four_meme] GMGN查询异常(跳过): {e}")

        logger.info(
            f"[gmgn] {addr[:10]} holders={result['holder_count']} "
            f"bot_degen={result['bot_degen_count']} creator_total={result['creator_created_count']} "
            f"creator_hold={result['creator_hold_rate']} top10={result['top_10_holder_rate']} "
            f"alert={result['is_show_alert']} "
            f"tw={result['has_twitter']} tg={result['has_telegram']} dc={result['has_discord']}"
        )
        return result

    async def _analyze_four_meme(self, token_address: str, deployer_address: str) -> Dict[str, Any]:
        """Four.Meme 专用分析逻辑（Flagent模型，2026-03-08更新）

        基础分60，买入门槛75（config.yaml four_meme.min_score）

        硬拒绝：DS警告 / 貔貅 / 持有人<20 / Dev发币>=50 / 模拟卖出失败 / 税率>15%
        加分：持有人多(+5~15) / Bot Degen(+3~10) / 社交媒体(+5~8) / Dev持仓低(+4~8) / Top10分散(+4~8) / 零税(+5) / 模拟卖出成功(+10)
        扣分：无社交(-15) / 持有人少(-5~15) / Dev发币多(-10) / Top10集中(-10~20) / Dev持仓高(-8)
        """
        start_time = time.time()
        token_address = token_address.lower()
        deployer_address = (deployer_address or "").lower()

        logger.info(f"[four_meme] 开始分析: {token_address[:10]}...")

        result = {
            "token_address": token_address,
            "final_score": 60,
            "decision": "reject",
            "risk_items": [],
            "bonus_items": [],
            "raw_data": {},
            "analysis_time": 0,
            "platform": "four_meme"
        }

        score = 60
        risk_items = []
        bonus_items = []

        def _hard_reject(desc: str):
            result["final_score"] = 0
            result["decision"] = "reject"
            result["risk_items"] = risk_items + [{"desc": desc, "score": -100}]
            result["analysis_time"] = time.time() - start_time
            logger.warning(f"[four_meme] 拒绝 {token_address[:10]}: {desc}")

        # ── 1. Deployer 黑名单（硬拒绝）──
        if deployer_address:
            try:
                bl_reason = await self.blacklist_manager.check_deployer(deployer_address)
                if bl_reason:
                    result["raw_data"]["blacklist"] = bl_reason
                    _hard_reject(f"Deployer 黑名单: {bl_reason}")
                    return result
            except Exception as e:
                logger.warning(f"[four_meme] 黑名单检查失败(跳过): {e}")

        # ── 1b. Deployer 历史 rug 率（硬拒绝 + 自动黑名单）──
        deployer_history = await self._check_deployer_history(deployer_address)
        result["raw_data"]["deployer_history"] = deployer_history
        if not deployer_history.get("passed", True):
            reason = deployer_history.get("reason", "Deployer历史rug率过高")
            # 自动将连续rug的Dev加入黑名单
            if deployer_address:
                try:
                    await self.blacklist_manager.add_deployer(deployer_address, reason)
                    logger.warning(f"[四meme] Dev已加入黑名单: {deployer_address[:10]}... 原因: {reason}")
                except Exception as e:
                    logger.debug(f"[四meme] 黑名单写入失败(跳过): {e}")
            _hard_reject(reason)
            return result

        # ── 2. 并行执行三项外部检测（GMGN + Honeypot.is + GoPlus）──
        gmgn_task = self._check_gmgn_signals(token_address)
        honeypot_task = self.check_honeypot_is(token_address)
        goplus_task = self.check_goplus(token_address)

        gmgn_res, honeypot_res, goplus_raw = await asyncio.gather(
            gmgn_task, honeypot_task, goplus_task, return_exceptions=True
        )
        if isinstance(gmgn_res, Exception):
            logger.warning(f"[four_meme] GMGN 检测异常(跳过): {gmgn_res}")
            gmgn_res = {}
        if isinstance(honeypot_res, Exception):
            logger.warning(f"[four_meme] Honeypot.is 检测失败(跳过): {honeypot_res}")
            honeypot_res = {}
        if isinstance(goplus_raw, Exception):
            logger.warning(f"[four_meme] GoPlus 检测失败(跳过): {goplus_raw}")
            goplus_raw = {}

        result["raw_data"]["gmgn"] = gmgn_res
        result["raw_data"]["honeypot"] = honeypot_res
        result["raw_data"]["goplus"] = goplus_raw

        # ═══════════════════════════════════════════════════════
        # ── 3. GMGN 硬拒绝条件 ──
        # ═══════════════════════════════════════════════════════

        # DS危险警告
        if gmgn_res.get("is_show_alert"):
            flags = gmgn_res.get("flags") or []
            _hard_reject(f"GMGN危险警告(DS): {flags if flags else '未知风险'}")
            return result

        # 持有人极少（<20 = 极高风险）
        holder_count = gmgn_res.get("holder_count")
        if holder_count is not None and holder_count < 20:
            _hard_reject(f"持有人不足20个({holder_count}人)，极高风险")
            return result

        # Dev发币总数过多（serial rugger）
        creator_total = gmgn_res.get("creator_created_count")
        if creator_total is not None and creator_total >= 50:
            _hard_reject(f"Dev发币总数过多: {creator_total}个（>=50拒绝）")
            return result

        # ── 4. Honeypot.is 貔貅检测（硬拒绝）──
        if honeypot_res.get("isHoneypot"):
            issue = honeypot_res.get("honeypotResult", {}).get("issue", "未知原因")
            _hard_reject(f"Honeypot.is 确认貔貅: {issue}")
            return result

        # ── 5. 模拟卖出（硬拒绝或加分）──
        if honeypot_res:
            if not honeypot_res.get("simulationSuccess", True):
                _hard_reject("模拟卖出失败，无法验证可卖出性")
                return result
            score += 10
            bonus_items.append({"desc": "模拟卖出成功", "score": +10})

        # ── 6. 税率检测 ──
        # 优先使用 honeypot.is 模拟值，fallback 到 GMGN mutil_window 的静态值
        try:
            sim = honeypot_res.get("simulationResult", {}) if honeypot_res else {}
            buy_tax = float(sim.get("buyTax") or gmgn_res.get("buy_tax") or 0)
            sell_tax = float(sim.get("sellTax") or gmgn_res.get("sell_tax") or 0)
            if sell_tax > 15:
                _hard_reject(f"卖出税过高: {sell_tax:.1f}%（>15%拒绝）")
                return result
            elif sell_tax > 5:
                score -= 10
                risk_items.append({"desc": f"卖出税偏高: {sell_tax:.1f}%", "score": -10})
            elif buy_tax <= 1 and sell_tax <= 1:
                score += 5
                bonus_items.append({"desc": "零税率", "score": +5})
        except Exception as e:
            logger.warning(f"[four_meme] 税率解析失败(跳过): {e}")

        # ── 7. GoPlus 貔貅（硬拒绝）──
        if goplus_raw:
            if int(goplus_raw.get("is_honeypot", 0)) == 1:
                _hard_reject("GoPlus 确认貔貅")
                return result
            if goplus_raw.get("cannot_sell_all") == "1":
                _hard_reject("GoPlus: 无法卖出全部代币（貔貅特征）")
                return result

        # ═══════════════════════════════════════════════════════
        # ── 8. GMGN 综合评分（Flagent模型）──
        # ═══════════════════════════════════════════════════════

        # 8a. 持有人数量
        if holder_count is not None:
            if holder_count >= 100:
                score += 15
                bonus_items.append({"desc": f"持有人多: {holder_count}人(>=100)", "score": +15})
            elif holder_count >= 50:
                score += 10
                bonus_items.append({"desc": f"持有人较多: {holder_count}人(>=50)", "score": +10})
            elif holder_count >= 30:
                score += 5
                bonus_items.append({"desc": f"持有人尚可: {holder_count}人(>=30)", "score": +5})
            else:  # 20-29
                score -= 5
                risk_items.append({"desc": f"持有人偏少: {holder_count}人(<30)", "score": -5})

        # 8b. Bot Degen（专业机器人认可度）
        bot_degen = gmgn_res.get("bot_degen_count")
        if bot_degen is not None:
            if bot_degen >= 100:
                score += 10
                bonus_items.append({"desc": f"Bot Degen高认可({bot_degen})", "score": +10})
            elif bot_degen >= 50:
                score += 7
                bonus_items.append({"desc": f"Bot Degen认可({bot_degen})", "score": +7})
            elif bot_degen >= 20:
                score += 3
                bonus_items.append({"desc": f"Bot Degen少量认可({bot_degen})", "score": +3})

        # 8c. 社交媒体
        has_social = gmgn_res.get("has_twitter") or gmgn_res.get("has_telegram") or gmgn_res.get("has_discord")
        if gmgn_res.get("has_twitter"):
            score += 8
            bonus_items.append({"desc": "有Twitter", "score": +8})
        if gmgn_res.get("has_telegram"):
            score += 5
            bonus_items.append({"desc": "有Telegram", "score": +5})
        if gmgn_res.get("has_discord"):
            score += 5
            bonus_items.append({"desc": "有Discord", "score": +5})
        if not has_social:
            score -= 15
            risk_items.append({"desc": "无任何社交媒体", "score": -15})

        # 8d. Dev持仓比例
        creator_hold = gmgn_res.get("creator_hold_rate")
        if creator_hold is not None:
            if creator_hold < 0.02:
                score += 8
                bonus_items.append({"desc": f"Dev持仓极低({creator_hold*100:.1f}%<2%)", "score": +8})
            elif creator_hold < 0.05:
                score += 4
                bonus_items.append({"desc": f"Dev持仓低({creator_hold*100:.1f}%<5%)", "score": +4})
            elif creator_hold > 0.10:
                score -= 8
                risk_items.append({"desc": f"Dev持仓高({creator_hold*100:.1f}%>10%)", "score": -8})

        # 8e. Top10集中度
        top10 = gmgn_res.get("top_10_holder_rate")
        if top10 is not None:
            top10_pct = top10 * 100 if top10 <= 1.0 else top10
            if top10_pct < 20:
                score += 8
                bonus_items.append({"desc": f"Top10集中度低({top10_pct:.1f}%<20%)", "score": +8})
            elif top10_pct < 30:
                score += 4
                bonus_items.append({"desc": f"Top10集中度适中({top10_pct:.1f}%<30%)", "score": +4})
            elif top10_pct > 60:
                score -= 20
                risk_items.append({"desc": f"Top10严重集中({top10_pct:.1f}%>60%)", "score": -20})
            elif top10_pct > 40:
                score -= 10
                risk_items.append({"desc": f"Top10集中({top10_pct:.1f}%>40%)", "score": -10})

        # 8f. Dev发币总数（适量加分，过多已在硬拒绝处理）
        if creator_total is not None:
            if creator_total > 20:
                score -= 10
                risk_items.append({"desc": f"Dev发币较多({creator_total}个>20)", "score": -10})
            elif creator_total <= 10:
                score += 5
                bonus_items.append({"desc": f"Dev发币适量({creator_total}个)", "score": +5})

        # ── 9. 评分区间限制 ──
        score = max(0, min(100, score))

        # ── 10. 决策（从config读门槛，默认75）──
        min_score = 75  # 与 config.yaml four_meme.min_score 一致
        if score >= min_score:
            decision = "buy"
        elif score >= min_score - 10:
            decision = "half_buy"
        else:
            decision = "reject"

        result["final_score"] = score
        result["decision"] = decision
        result["risk_items"] = risk_items
        result["bonus_items"] = bonus_items
        result["analysis_time"] = time.time() - start_time

        logger.info(f"[four_meme] 分析完成: {token_address[:10]}... Score={score} Decision={decision}"
                    + (f" 风险: {[r['desc'] for r in risk_items]}" if risk_items else "")
                    + (f" 加分: {[b['desc'] for b in bonus_items]}" if bonus_items else ""))
        return result

    async def analyze(self, token_address: str, deployer_address: str, pair_address: str = None, initial_state: Dict[str, Any] = None, platform: str = None) -> Dict[str, Any]:
        """执行完整安全分析（全并行版：本地检测与外部API同时发起）"""
        
        if platform == 'four_meme':
             return await self._analyze_four_meme(token_address, deployer_address)

        start_time = time.time()
        t_perf = time.perf_counter()
        token_address = token_address.lower()
        deployer_address = deployer_address.lower() if deployer_address else ""

        # Ensure DB is ready (idempotent, very fast)
        await self.blacklist_manager.init_db()

        result = {
            "token_address": token_address,
            "final_score": 100,
            "decision": "reject",
            "risk_items": [],
            "bonus_items": [],
            "raw_data": {},
            "analysis_time": 0
        }
        score = 100
        risk_items = []
        bonus_items = []

        # ── 全并行：本地检测 + 所有外部API同时发起 ──
        async def _timed(coro, name):
            t = time.perf_counter()
            try:
                r = await coro
                logger.info(f"⏱️ [{name}]: {(time.perf_counter()-t)*1000:.0f}ms")
                return r
            except Exception as e:
                logger.info(f"⏱️ [{name}]: {(time.perf_counter()-t)*1000:.0f}ms (failed: {type(e).__name__})")
                raise

        raw_results = await asyncio.gather(
            _timed(self._task_local_checks(token_address, deployer_address, pair_address), "local_checks"),
            _timed(self.check_goplus(token_address),                                        "goplus"),
            _timed(self.check_honeypot_is(token_address),                                   "honeypot"),
            _timed(self.check_contract_code(token_address),                                 "contract"),
            _timed(self.analyze_token_holders(token_address, deployer_address, pair_address), "holders"),
            _timed(self.analyze_deployer_history(deployer_address),                         "deployer"),
            _timed(self.check_bytecode_similarity(token_address),                           "similarity"),
            _timed(self.analyze_buyer_fund_source(pair_address, token_address),             "fund_source"),
            _timed(self.check_deployer_token_retention(token_address, deployer_address),    "retention"),
            _timed(self.analyze_observation(token_address, pair_address, initial_state),    "observation"),
            return_exceptions=True
        )
        logger.info(f"⏱️ 全并行总耗时: {(time.perf_counter()-t_perf)*1000:.0f}ms")

        def _unwrap(val, default):
            if isinstance(val, Exception):
                logger.error(f"安全检测子任务异常: {val}")
                return default
            return val if val is not None else default

        local_data        = _unwrap(raw_results[0], {})
        goplus_data       = _unwrap(raw_results[1], {})
        honeypot_data     = _unwrap(raw_results[2], {})
        contract_data     = _unwrap(raw_results[3], {})
        holders_data      = _unwrap(raw_results[4], {})
        deployer_data     = _unwrap(raw_results[5], {})
        similarity_reason = _unwrap(raw_results[6], None)
        fund_source_data  = _unwrap(raw_results[7], {})
        retention_data    = _unwrap(raw_results[8], {})
        observation_data  = _unwrap(raw_results[9], {})

        # 优先处理本地拒绝信号（黑名单/模拟失败）
        if local_data.get("reject"):
            score = 0
            risk_items.append({"desc": local_data.get("reason", "本地检测拒绝"), "score": -100})
            return self._finalize_result(result, score, risk_items, bonus_items, start_time)

        result["raw_data"]["simulation"] = local_data.get("simulation", {})
        if local_data.get("sim_bonus"):
            bonus_items.append({"desc": "本地模拟交易成功 (eth_call)", "score": +20})
        
        # --- Fallback for Holders Check (GoPlus) ---
        if not holders_data and goplus_data and goplus_data.get("holders"):
            logger.info(f"Using GoPlus holders data as fallback for {token_address}")
            logger.info(f"GoPlus Fallback Data: {json.dumps(goplus_data.get('holders'))[:200]}...") # Log first 200 chars
            try:
                gp_holders = goplus_data["holders"]
                top_5 = 0
                max_single = 0
                deployer_sh = 0
                
                # GoPlus percent is ratio (e.g. 0.05 = 5%)
                # 过滤零地址、burn地址、LP地址、锁仓合约地址、合约地址（is_contract==1）
                filtered_holders = []
                for h in gp_holders:
                    addr = h.get("address", "").lower()
                    if addr in ["0x0000000000000000000000000000000000000000", "0x000000000000000000000000000000000000dead"]: continue
                    if pair_address and addr == pair_address.lower(): continue
                    if addr in [p.lower() for p in LOCK_PLATFORMS.values()]: continue
                    if h.get('is_contract') == 1:  # 过滤LP合约等合约地址，只统计真实钱包
                        continue
                    filtered_holders.append(h)

                # 过滤后无真实钱包 → 新币上线初期正常现象，用哨兵值替代 None，后续降权评分
                if not filtered_holders:
                    logger.info(f"GoPlus holders fallback: 过滤合约后无真实钱包持仓（新币初期）")
                    holders_data = {"no_real_holders": True, "top_5_share": 0, "max_single_share": 0, "deployer_share": 0, "holders_count": 0}
                else:
                    for i, h in enumerate(filtered_holders):
                        if i >= 20: break
                        pct = float(h.get("percent", 0)) * 100

                        if i < 5: top_5 += pct
                        if pct > max_single: max_single = pct
                        if deployer_address and h.get("address", "").lower() == deployer_address.lower():
                            deployer_sh = pct

                    holders_data = {
                        "top_5_share": top_5,
                        "max_single_share": max_single,
                        "deployer_share": deployer_sh,
                        "holders_count": goplus_data.get("holder_count", len(filtered_holders))
                    }
            except Exception as e:
                logger.warning(f"GoPlus holders fallback failed: {e}")

        # --- Fail-Safe Mechanism ---
        # 必须检测项失败 → 直接拒绝
        check_results_map = {
            "goplus": goplus_data,
            "honeypot": honeypot_data,
            "contract": contract_data,
            "holders": holders_data
        }
        
        for check_name in REQUIRED_CHECKS:
            if not check_results_map.get(check_name):
                logger.error(f"Critical Security Check Failed: {check_name} (API Error or Empty Result)")
                score = 0
                risk_items.append({"desc": f"必须检测项失败: {check_name}", "score": -100})
                return self._finalize_result(result, score, risk_items, bonus_items, start_time)

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

            # creator持有99%+代币 → 无法分发给真实买家 / 合约限制卖出，硬拒绝
            creator_pct = float(goplus_data.get("creator_percent", 0) or 0)
            if creator_pct >= 0.99:
                score = 0
                risk_items.append({"desc": f"GoPlus: creator持有{creator_pct*100:.0f}%代币（貔貅/Rug特征）", "score": -100})
                logger.warning(f"creator_percent={creator_pct:.2f} 硬拒绝: {token_address[:10]}")
                return self._finalize_result(result, score, risk_items, bonus_items, start_time)

            # 唯一持仓是LP合约(100%) → 代币无法被真实钱包持有，硬拒绝
            holder_count = int(goplus_data.get("holder_count", 99) or 99)
            holders = goplus_data.get("holders", [])
            if holder_count <= 2 and holders:
                non_lp_real_holders = [
                    h for h in holders
                    if h.get("is_contract") == 0
                    or float(h.get("percent", 0)) < 0.8
                ]
                if not non_lp_real_holders:
                    score = 0
                    risk_items.append({"desc": f"GoPlus: 无真实钱包持仓({holder_count}个holder全为合约/LP)，貔貅特征", "score": -100})
                    logger.warning(f"holder_count={holder_count} 全为LP合约，硬拒绝: {token_address[:10]}")
                    return self._finalize_result(result, score, risk_items, bonus_items, start_time)

            # 无法卖出 / 交易暂停 / 冷却 → 直接硬拒绝
            if goplus_data.get("cannot_sell_all") == "1":
                score = 0
                risk_items.append({"desc": "GoPlus: 无法卖出全部代币（貔貅）", "score": -100})
                return self._finalize_result(result, score, risk_items, bonus_items, start_time)

            if goplus_data.get("transfer_pausable") == "1":
                score = 0
                risk_items.append({"desc": "GoPlus: 转账可被暂停（貔貅风险）", "score": -100})
                return self._finalize_result(result, score, risk_items, bonus_items, start_time)

            if goplus_data.get("trading_cooldown") == "1":
                score -= 25
                risk_items.append({"desc": "GoPlus: 存在交易冷却限制", "score": -25})

            if goplus_data.get("personal_slippage_modifiable") == "1":
                score -= 20
                risk_items.append({"desc": "GoPlus: 可针对个人地址修改滑点（貔貅特征）", "score": -20})

        # 2. Honeypot.is 检测
        if honeypot_data:
            if not honeypot_data.get("simulationSuccess", False):
                score = 0
                risk_items.append({"desc": "Honeypot.is 模拟交易失败", "score": -100})

            # 已知貔貅模板特征：decimals=8 + totalSupply=None（Honeypot.is读不出）
            # BDAG/PIPPKIN 等同一工厂部署的合约，buyGas=154480 sellGas=107848
            hp_token = honeypot_data.get("token", {}) or {}
            hp_sim = honeypot_data.get("simulationResult", {}) or {}
            hp_decimals = hp_token.get("decimals")
            hp_total_supply = hp_token.get("totalSupply")
            hp_buy_gas = str(hp_sim.get("buyGas", ""))
            hp_sell_gas = str(hp_sim.get("sellGas", ""))
            KNOWN_HP_BUY_GAS = {"154480"}
            KNOWN_HP_SELL_GAS = {"107848"}
            if (hp_decimals == 8
                    and hp_total_supply is None
                    and hp_buy_gas in KNOWN_HP_BUY_GAS
                    and hp_sell_gas in KNOWN_HP_SELL_GAS):
                score = 0
                risk_items.append({"desc": f"Honeypot.is: 匹配已知貔貅合约模板(decimals=8,totalSupply=None,gas={hp_buy_gas}/{hp_sell_gas})", "score": -100})
                logger.warning(f"已知貔貅模板匹配，硬拒绝: {token_address[:10]}")
                return self._finalize_result(result, score, risk_items, bonus_items, start_time)

            # 再次检查税率 (双重确认)
            hp_buy_tax = float(hp_sim.get("buyTax", 0))
            hp_sell_tax = float(hp_sim.get("sellTax", 0))

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
        
        # 5. 持仓集中度分析
        if holders_data:
            if holders_data.get("no_real_holders"):
                # 新币初期：所有持仓均在合约（LP等），暂无真实钱包，降权而非拒绝
                score -= 15
                risk_items.append({"desc": "暂无真实钱包持仓（新币上线初期）", "score": -15})
            else:
                top_5 = holders_data.get("top_5_share", 0)
                max_single = holders_data.get("max_single_share", 0)
                deployer_hold = holders_data.get("deployer_share", 0)

                if top_5 > 30:
                    score -= 40
                    risk_items.append({"desc": f"持仓高度集中 (Top 5: {top_5:.1f}%)", "score": -40})

                if max_single > 15:
                    score -= 30
                    risk_items.append({"desc": f"存在巨鲸持仓 (Single: {max_single:.1f}%)", "score": -30})

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
