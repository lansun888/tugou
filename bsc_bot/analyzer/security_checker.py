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
GMGN_SECURITY_URL = "https://gmgn.ai/defi/quotation/v1/tokens/security"
GMGN_CHAIN = "bsc"
# 升级到 BSCScan V2 API (使用 Etherscan V2 统一端点)
BSCSCAN_API_URL = "https://api.etherscan.io/v2/api?chainid=56" 
BSCSCAN_API_KEY = os.getenv("BSCSCAN_API_KEY", "")
GMGN_TOKEN_STAT_URL = "https://gmgn.ai/api/v1/token_stat/bsc"
DEXSCREENER_PAIRS_URL = "https://api.dexscreener.com/token-pairs/v1/bsc"

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

    async def check_gmgn(self, token_address: str) -> Dict[str, Any]:
        """GMGN Security API 检测（独立貔貅数据库，感知最快）"""
        cache_key = f"gmgn:{token_address.lower()}"
        cached = _cache.get(cache_key, max_age_seconds=300)  # 5 min
        if cached is not None:
            logger.debug(f"[cache] gmgn hit: {token_address[:10]}…")
            return cached

        try:
            session = await self._get_session()
            url = f"{GMGN_SECURITY_URL}/{GMGN_CHAIN}/{token_address}"
            headers = {
                "Referer": "https://gmgn.ai/",
                "Origin": "https://gmgn.ai",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
            }
            async with session.get(url, headers=headers, timeout=3) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # GMGN 响应结构: {"code":0,"msg":"success","data":{"goplus":{...}}}
                    # 提取 data.goplus 作为实际安全数据（GoPlus 格式）
                    goplus_payload = data.get("data", {}).get("goplus") or {}
                    logger.debug(f"GMGN Raw Data for {token_address}: {json.dumps(goplus_payload)[:400]}")
                    if goplus_payload:
                        _cache.set(cache_key, goplus_payload)
                    return goplus_payload
                elif resp.status == 403:
                    # Cloudflare TLS 指纹拦截，Python aiohttp 特有，降级跳过
                    logger.debug(f"GMGN API 403(Cloudflare)，跳过: {token_address[:10]}")
                else:
                    logger.warning(f"GMGN API 返回非200状态: {resp.status} for {token_address[:10]}")
        except asyncio.TimeoutError:
            logger.warning(f"GMGN API 超时(>3s)，跳过: {token_address[:10]}")
        except Exception as e:
            logger.warning(f"GMGN API 检测失败: {e}")
        return {}

    async def check_gmgn_token_stat(self, token_address: str) -> Dict[str, Any]:
        """GMGN Token Stat API：持有人数、老鼠仓占比、DEV持仓等"""
        cache_key = f"gmgn_stat:{token_address.lower()}"
        cached = _cache.get(cache_key, max_age_seconds=120)
        if cached is not None:
            logger.debug(f"[cache] gmgn_stat hit: {token_address[:10]}…")
            return cached
        try:
            session = await self._get_session()
            url = f"{GMGN_TOKEN_STAT_URL}/{token_address}"
            headers = {
                "Referer": "https://gmgn.ai/",
                "Origin": "https://gmgn.ai",
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            }
            async with session.get(url, headers=headers, timeout=3) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result = data.get("data", {}) or {}
                    logger.debug(f"GMGN token_stat for {token_address[:10]}: {json.dumps(result)[:300]}")
                    if result:
                        _cache.set(cache_key, result)
                    return result
                elif resp.status == 403:
                    logger.debug(f"GMGN token_stat 403(Cloudflare)，跳过: {token_address[:10]}")
        except asyncio.TimeoutError:
            logger.warning(f"GMGN token_stat 超时(>3s)，跳过: {token_address[:10]}")
        except Exception as e:
            logger.warning(f"GMGN token_stat 失败: {e}")
        return {}

    async def check_price_behavior(self, token_address: str) -> Dict[str, Any]:
        """
        通过DexScreener数据检测异常价格行为
        - 24h跌幅超85%：疑似砸盘
        - 5分钟涨幅超500% + 1h跌幅超80%：典型拉盘砸盘
        - 6h跌幅超90%：极端崩盘
        超时3秒降级处理，不阻塞主流程
        """
        cache_key = f"price_behavior:{token_address.lower()}"
        cached = _cache.get(cache_key, max_age_seconds=60)
        if cached is not None:
            logger.debug(f"[cache] price_behavior hit: {token_address[:10]}…")
            return cached

        result = {
            "reject": False,
            "reason": None,
            "price_change_5m": 0.0,
            "price_change_1h": 0.0,
            "price_change_6h": 0.0,
            "price_change_24h": 0.0,
            "liquidity_usd": 0.0,
            "token_age_minutes": None,
        }
        try:
            session = await self._get_session()
            url = f"{DEXSCREENER_PAIRS_URL}/{token_address}"
            async with session.get(url, timeout=3) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    pair_list = data if isinstance(data, list) else data.get("pairs", [])
                    if not pair_list:
                        return result
                    best_pair = max(pair_list, key=lambda x: float((x.get("liquidity") or {}).get("usd", 0) or 0))
                    pc = best_pair.get("priceChange") or {}
                    result["price_change_5m"]  = float(pc.get("m5",  0) or 0)
                    result["price_change_1h"]  = float(pc.get("h1",  0) or 0)
                    result["price_change_6h"]  = float(pc.get("h6",  0) or 0)
                    result["price_change_24h"] = float(pc.get("h24", 0) or 0)
                    result["liquidity_usd"]    = float((best_pair.get("liquidity") or {}).get("usd", 0) or 0)
                    pair_created_at = best_pair.get("pairCreatedAt")
                    if pair_created_at:
                        result["token_age_minutes"] = (time.time() * 1000 - pair_created_at) / 60000
                    # ── 硬拒绝规则 ──
                    if result["price_change_24h"] <= -85:
                        result["reject"] = True
                        result["reason"] = f"价格从高点回撤超85% (24h: {result['price_change_24h']:.1f}%)"
                    elif result["price_change_5m"] >= 500 and result["price_change_1h"] <= -80:
                        result["reject"] = True
                        result["reason"] = f"典型拉盘砸盘形态 (5m: +{result['price_change_5m']:.0f}%, 1h: {result['price_change_1h']:.0f}%)"
                    elif result["price_change_6h"] <= -90:
                        result["reject"] = True
                        result["reason"] = f"6小时内价格崩溃超90% (6h: {result['price_change_6h']:.1f}%)"
                    _cache.set(cache_key, result)
        except asyncio.TimeoutError:
            logger.warning(f"check_price_behavior 超时(>3s)，跳过: {token_address[:10]}")
        except Exception as e:
            logger.warning(f"check_price_behavior 失败: {e}")
        return result

    async def check_holder_structure(self, token_address: str) -> Dict[str, Any]:
        """
        检测持仓结构是否健康（使用GoPlus数据）
        - top1 >= 40%：硬拒绝
        - top2 >= 65%：硬拒绝
        - top10 >= 80%：扣20分
        超时3秒降级处理，不阻塞主流程
        """
        cache_key = f"holder_struct:{token_address.lower()}"
        cached = _cache.get(cache_key, max_age_seconds=120)
        if cached is not None:
            logger.debug(f"[cache] holder_struct hit: {token_address[:10]}…")
            return cached

        result = {
            "reject": False,
            "reason": None,
            "top1_holder_rate": 0.0,
            "top2_holder_combined_rate": 0.0,
            "top10_holder_rate": 0.0,
            "score_deduct": 0,
            "deduct_reason": None,
        }
        try:
            goplus_data = await self.check_goplus(token_address)
            if not goplus_data:
                return result
            holders = goplus_data.get("holders", [])
            if not holders:
                return result
            DEAD = "0x000000000000000000000000000000000000dead"
            ZERO = "0x0000000000000000000000000000000000000000"
            real_rates = []
            for h in holders:
                addr = h.get("address", "").lower()
                if addr in [DEAD, ZERO]:
                    continue
                if addr in [p.lower() for p in LOCK_PLATFORMS.values()]:
                    continue
                if h.get("is_contract") == 1:
                    continue
                pct = float(h.get("percent", 0) or 0)
                real_rates.append(pct)
            if not real_rates:
                return result
            real_rates.sort(reverse=True)
            top1  = real_rates[0]
            top2  = sum(real_rates[:2]) if len(real_rates) >= 2 else top1
            top10 = sum(real_rates[:10])
            result["top1_holder_rate"]          = top1
            result["top2_holder_combined_rate"] = top2
            result["top10_holder_rate"]         = top10
            # ── 硬拒绝规则 ──
            if top1 >= 0.40:
                result["reject"] = True
                result["reason"] = f"单地址持仓超40% ({top1*100:.1f}%)"
            elif top2 >= 0.65:
                result["reject"] = True
                result["reason"] = f"Top2地址合计持仓超65% ({top2*100:.1f}%)"
            # ── 扣分规则 ──
            if not result["reject"] and top10 >= 0.80:
                result["score_deduct"] = -20
                result["deduct_reason"] = f"Top10持仓过于集中 ({top10*100:.1f}%)"
            _cache.set(cache_key, result)
        except asyncio.TimeoutError:
            logger.warning(f"check_holder_structure 超时，跳过: {token_address[:10]}")
        except Exception as e:
            logger.warning(f"check_holder_structure 失败: {e}")
        return result

    @staticmethod
    def _get_min_liquidity_threshold(token_age_minutes: Optional[float]) -> float:
        """动态流动性门槛（基于代币年龄）"""
        if token_age_minutes is None:
            return 2000.0
        if token_age_minutes < 10:
            return 10000.0
        elif token_age_minutes < 60:
            return 5000.0
        else:
            return 2000.0

    def _log_rejection(self, token_address: str, reason: str, data_snapshot: dict):
        """结构化拒绝日志（JSON格式）"""
        logger.warning(
            "REJECTED " + json.dumps({
                "token": token_address,
                "action": "REJECTED",
                "reason": reason,
                "data_snapshot": data_snapshot,
            }, ensure_ascii=False)
        )

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

    async def _analyze_four_meme(self, token_address: str, deployer_address: str) -> Dict[str, Any]:
        """Four.Meme 专用分析逻辑

        基础分 75（已考虑平台特性）：
        - 合约未开源：平台统一模板，不扣分
        - 权限未放弃：平台机制，不扣分
        - 流动性未锁定：bonding curve 机制，不扣分
        只检测真正有意义的风险：貔貅、税率、持仓集中、黑名单
        0 分仅在确认貔貅时出现。
        """
        start_time = time.time()
        token_address = token_address.lower()
        deployer_address = (deployer_address or "").lower()
        FOUR_MEME_CONTRACT = "0x5c952063c7fc8610ffdb798152d69f0b9550762b"

        logger.info(f"[four_meme] 开始分析: {token_address[:10]}...")

        result = {
            "token_address": token_address,
            "final_score": 75,
            "decision": "reject",
            "risk_items": [],
            "bonus_items": [],
            "raw_data": {},
            "analysis_time": 0,
            "platform": "four_meme"
        }

        # 基础分 75，反映平台已做基础筛选
        score = 75
        risk_items = []
        bonus_items = []

        # ── 1. deployer 黑名单（硬拒绝）──
        if deployer_address:
            try:
                bl_reason = await self.blacklist_manager.check_deployer(deployer_address)
                if bl_reason:
                    score = 0
                    risk_items.append({"desc": f"Deployer 黑名单: {bl_reason}", "score": -100})
                    logger.warning(f"[four_meme] Deployer 黑名单拒绝: {deployer_address[:10]}")
                    result["raw_data"]["blacklist"] = bl_reason
                    result["final_score"] = 0
                    result["decision"] = "reject"
                    result["risk_items"] = risk_items
                    result["analysis_time"] = time.time() - start_time
                    return result
            except Exception as e:
                logger.warning(f"[four_meme] 黑名单检查失败(跳过): {e}")

        # ── 2. 貔貅+行为检测（并行，全部3秒超时）──
        honeypot_res = {}
        goplus_res = {}
        gmgn_res = {}
        gmgn_stat_res = {}
        price_beh_res = {}
        holder_stru_res = {}
        try:
            _gr = await asyncio.gather(
                asyncio.wait_for(self.check_honeypot_is(token_address),     timeout=3.0),
                asyncio.wait_for(self.check_goplus(token_address),          timeout=3.0),
                asyncio.wait_for(self.check_gmgn(token_address),            timeout=3.0),
                asyncio.wait_for(self.check_gmgn_token_stat(token_address), timeout=3.0),
                asyncio.wait_for(self.check_price_behavior(token_address),  timeout=3.0),
                asyncio.wait_for(self.check_holder_structure(token_address), timeout=3.0),
                return_exceptions=True,
            )
            def _e(v, d): return v if not isinstance(v, Exception) else d
            honeypot_res    = _e(_gr[0], {})
            goplus_res      = _e(_gr[1], {})
            gmgn_res        = _e(_gr[2], {})
            gmgn_stat_res   = _e(_gr[3], {})
            price_beh_res   = _e(_gr[4], {})
            holder_stru_res = _e(_gr[5], {})
        except Exception as e:
            logger.warning(f"[four_meme] 并行检测异常(跳过): {e}")

        result["raw_data"]["gmgn"] = gmgn_res

        # ── 2a-0. GMGN 确认貔貅（优先级最高，数据库更新最快）──
        if gmgn_res:
            if gmgn_res.get("is_honeypot") is True:
                score = 0
                risk_items.append({"desc": "GMGN 确认貔貅", "score": -100})
                logger.warning(f"[four_meme] GMGN 确认貔貅: {token_address[:10]}")
                result["final_score"] = 0
                result["decision"] = "reject"
                result["risk_items"] = risk_items
                result["analysis_time"] = time.time() - start_time
                return result
            rug_ratio = float(gmgn_res.get("rug_ratio", 0) or 0)
            if rug_ratio >= 0.8:
                score = 0
                risk_items.append({"desc": f"GMGN: 高Rug概率 ({rug_ratio*100:.0f}%)", "score": -100})
                logger.warning(f"[four_meme] GMGN 高Rug概率 {rug_ratio:.2f} 拒绝: {token_address[:10]}")
                result["final_score"] = 0
                result["decision"] = "reject"
                result["risk_items"] = risk_items
                result["analysis_time"] = time.time() - start_time
                return result
            if gmgn_res.get("is_blacklisted") is True:
                score = 0
                risk_items.append({"desc": "GMGN: 代币已被黑名单", "score": -100})
                logger.warning(f"[four_meme] GMGN 黑名单拒绝: {token_address[:10]}")
                result["final_score"] = 0
                result["decision"] = "reject"
                result["risk_items"] = risk_items
                result["analysis_time"] = time.time() - start_time
                return result
            top10_rate = float(gmgn_res.get("top_10_holder_rate", 0) or 0)
            if top10_rate >= 0.95:
                score = 0
                risk_items.append({"desc": f"GMGN: 前10持仓过度集中 ({top10_rate*100:.0f}%)", "score": -100})
                logger.warning(f"[four_meme] GMGN 持仓过度集中 {top10_rate:.2f} 拒绝: {token_address[:10]}")
                result["final_score"] = 0
                result["decision"] = "reject"
                result["risk_items"] = risk_items
                result["analysis_time"] = time.time() - start_time
                return result
            if gmgn_res.get("renounced") is False:
                score -= 10
                risk_items.append({"desc": "GMGN: 合约未放弃所有权", "score": -10})
            if gmgn_res.get("low_liquidity") is True:
                score -= 15
                risk_items.append({"desc": "GMGN: 流动性不足", "score": -15})

        # Honeypot.is 确认貔貅 → 硬拒绝，score=0
        # 注意：isHoneypot 字段在 honeypotResult 子对象内，而非顶层
        if honeypot_res.get("honeypotResult", {}).get("isHoneypot"):
            issue = honeypot_res.get("honeypotResult", {}).get("issue", "未知原因")
            score = 0
            risk_items.append({"desc": f"Honeypot.is 确认貔貅: {issue}", "score": -100})
            logger.warning(f"[four_meme] 确认貔貅: {token_address[:10]} issue={issue}")
            result["raw_data"]["honeypot"] = honeypot_res
            result["final_score"] = 0
            result["decision"] = "reject"
            result["risk_items"] = risk_items
            result["analysis_time"] = time.time() - start_time
            return result

        # GoPlus 确认貔貅 → 硬拒绝，score=0
        if goplus_res and int(goplus_res.get("is_honeypot", 0)) == 1:
            score = 0
            risk_items.append({"desc": "GoPlus 确认貔貅", "score": -100})
            logger.warning(f"[four_meme] GoPlus 确认貔貅: {token_address[:10]}")
            result["raw_data"]["goplus"] = goplus_res
            result["final_score"] = 0
            result["decision"] = "reject"
            result["risk_items"] = risk_items
            result["analysis_time"] = time.time() - start_time
            return result

        result["raw_data"]["honeypot"] = honeypot_res
        result["raw_data"]["goplus"] = goplus_res

        # ── 2b. simulationSuccess=False → 无法验证卖出，硬拒绝 ──
        if honeypot_res and not honeypot_res.get("simulationSuccess", True):
            score = 0
            risk_items.append({"desc": "Honeypot.is 模拟卖出失败（无法验证可卖出性）", "score": -100})
            logger.warning(f"[four_meme] simulationSuccess=False 拒绝: {token_address[:10]}")
            result["final_score"] = 0
            result["decision"] = "reject"
            result["risk_items"] = risk_items
            result["analysis_time"] = time.time() - start_time
            return result

        # ── 2c. API 无响应扣分 ──
        # 两个均无响应：扣20分
        if not honeypot_res and not goplus_res:
            score -= 20
            risk_items.append({"desc": "Honeypot.is/GoPlus 均无响应，代币未被API索引", "score": -20})
            logger.warning(f"[four_meme] 两个貔貅API均无响应，降低评分20分: {token_address[:10]}")
        # GoPlus 单独无响应（Honeypot.is有数据但GoPlus无数据）→ 扣15分
        # 修复漏洞：之前此场景无任何扣分，导致score=75直接买入
        elif not goplus_res and honeypot_res:
            score -= 15
            risk_items.append({"desc": "GoPlus 无响应（代币未被索引），Honeypot.is仅作参考", "score": -15})
            logger.warning(f"[four_meme] GoPlus无响应，扣15分: {token_address[:10]}")

        # ── 2d. GoPlus cannot_sell_all → 硬拒绝 ──
        if goplus_res and goplus_res.get("cannot_sell_all") == "1":
            score = 0
            risk_items.append({"desc": "GoPlus: 无法卖出全部代币（貔貅特征）", "score": -100})
            logger.warning(f"[four_meme] GoPlus cannot_sell_all 拒绝: {token_address[:10]}")
            result["final_score"] = 0
            result["decision"] = "reject"
            result["risk_items"] = risk_items
            result["analysis_time"] = time.time() - start_time
            return result

        # ── 2e. GMGN Token Stat 过滤 ──
        if gmgn_stat_res:
            _t10  = float(gmgn_stat_res.get("top_10_holder_rate", 0) or 0)
            _hcnt = int(gmgn_stat_res.get("holder_count", 0) or 0)
            _rat  = float(gmgn_stat_res.get("rat_trader_amount_percentage", 0) or 0)
            _dev  = float(gmgn_stat_res.get("creator_hold_rate", 0) or 0)
            _age  = price_beh_res.get("token_age_minutes") if price_beh_res else None
            _liq  = price_beh_res.get("liquidity_usd", 0) if price_beh_res else 0

            if _t10 >= 0.50:
                _snap = {"top10_rate": round(_t10, 4), "holder_count": _hcnt, "liquidity_usd": _liq}
                self._log_rejection(token_address, f"Top10持仓超50% ({_t10*100:.0f}%)", _snap)
                score = 0
                risk_items.append({"desc": f"Top10持仓超50%，筹码高度集中 ({_t10*100:.0f}%)", "score": -100})
                result["final_score"] = 0; result["decision"] = "reject"
                result["risk_items"] = risk_items; result["analysis_time"] = time.time() - start_time
                return result

            if _rat >= 0.30:
                _snap = {"rat_trader_ratio": round(_rat, 4), "top10_rate": round(_t10, 4), "holder_count": _hcnt}
                self._log_rejection(token_address, f"老鼠仓占比超30% ({_rat*100:.0f}%)", _snap)
                score = 0
                risk_items.append({"desc": f"老鼠仓占比超30% ({_rat*100:.0f}%)", "score": -100})
                result["final_score"] = 0; result["decision"] = "reject"
                result["risk_items"] = risk_items; result["analysis_time"] = time.time() - start_time
                return result

            if 0 < _hcnt <= 50:
                _snap = {"holder_count": _hcnt, "top10_rate": round(_t10, 4), "liquidity_usd": _liq}
                self._log_rejection(token_address, f"持有者数量不足50人 ({_hcnt})", _snap)
                score = 0
                risk_items.append({"desc": f"持有者不足50人 ({_hcnt})", "score": -100})
                result["final_score"] = 0; result["decision"] = "reject"
                result["risk_items"] = risk_items; result["analysis_time"] = time.time() - start_time
                return result

            if _dev < 0.001 and _age is not None and 0 < _age < 60:
                _snap = {"dev_holding_pct": round(_dev, 6), "token_age_min": round(_age, 1)}
                self._log_rejection(token_address, f"上线{_age:.0f}分钟内DEV已清仓", _snap)
                score = 0
                risk_items.append({"desc": f"上线60分钟内DEV已清仓（{_age:.0f}分钟）", "score": -100})
                result["final_score"] = 0; result["decision"] = "reject"
                result["risk_items"] = risk_items; result["analysis_time"] = time.time() - start_time
                return result

        # ── 2f. 价格行为过滤 ──
        if price_beh_res and price_beh_res.get("reject"):
            _reason = price_beh_res.get("reason", "价格异常")
            _snap = {
                "price_drop_pct": price_beh_res.get("price_change_24h", 0),
                "price_change_1h": price_beh_res.get("price_change_1h", 0),
                "price_change_5m": price_beh_res.get("price_change_5m", 0),
                "liquidity_usd": price_beh_res.get("liquidity_usd", 0),
                "token_age_min": price_beh_res.get("token_age_minutes"),
            }
            self._log_rejection(token_address, _reason, _snap)
            score = 0
            risk_items.append({"desc": _reason, "score": -100})
            result["final_score"] = 0; result["decision"] = "reject"
            result["risk_items"] = risk_items; result["analysis_time"] = time.time() - start_time
            return result

        # ── 2g. 动态流动性门槛 ──
        if price_beh_res:
            _liq_usd = float(price_beh_res.get("liquidity_usd", 0) or 0)
            _age_min = price_beh_res.get("token_age_minutes")
            _min_liq = self._get_min_liquidity_threshold(_age_min)
            if 0 < _liq_usd < _min_liq:
                _snap = {"liquidity_usd": round(_liq_usd, 2), "min_threshold": _min_liq, "token_age_min": round(_age_min, 1) if _age_min else None}
                self._log_rejection(token_address, f"流动性低于动态门槛 (${_liq_usd:.0f} < ${_min_liq:.0f})", _snap)
                score = 0
                risk_items.append({"desc": f"新代币流动性不足动态门槛 (${_liq_usd:.0f})", "score": -100})
                result["final_score"] = 0; result["decision"] = "reject"
                result["risk_items"] = risk_items; result["analysis_time"] = time.time() - start_time
                return result

        # ── 2h. 持仓结构过滤 ──
        if holder_stru_res and holder_stru_res.get("reject"):
            _reason = holder_stru_res.get("reason", "持仓结构异常")
            _snap = {
                "top1_holder_rate": round(holder_stru_res.get("top1_holder_rate", 0), 4),
                "top2_holder_combined_rate": round(holder_stru_res.get("top2_holder_combined_rate", 0), 4),
                "top10_holder_rate": round(holder_stru_res.get("top10_holder_rate", 0), 4),
            }
            self._log_rejection(token_address, _reason, _snap)
            score = 0
            risk_items.append({"desc": _reason, "score": -100})
            result["final_score"] = 0; result["decision"] = "reject"
            result["risk_items"] = risk_items; result["analysis_time"] = time.time() - start_time
            return result
        if holder_stru_res and holder_stru_res.get("score_deduct"):
            score += holder_stru_res["score_deduct"]
            risk_items.append({"desc": holder_stru_res.get("deduct_reason", "持仓集中扣分"), "score": holder_stru_res["score_deduct"]})

        # ── 3. 税率检测（GoPlus 或 Honeypot.is）──
        try:
            # GoPlus tax (decimal format: 0.05 = 5%)
            buy_tax = float(goplus_res.get("buy_tax", 0) or 0) * 100
            sell_tax = float(goplus_res.get("sell_tax", 0) or 0) * 100

            # Fallback to Honeypot.is simulation
            if buy_tax == 0 and sell_tax == 0 and honeypot_res:
                sim = honeypot_res.get("simulationResult", {})
                buy_tax = float(sim.get("buyTax", 0) or 0)
                sell_tax = float(sim.get("sellTax", 0) or 0)

            if buy_tax > 20 or sell_tax > 20:
                deduct = -30
                score += deduct
                risk_items.append({"desc": f"税率过高 (买:{buy_tax:.1f}% 卖:{sell_tax:.1f}%)", "score": deduct})
            elif buy_tax > 10 or sell_tax > 10:
                deduct = -15
                score += deduct
                risk_items.append({"desc": f"税率偏高 (买:{buy_tax:.1f}% 卖:{sell_tax:.1f}%)", "score": deduct})
        except Exception as e:
            logger.warning(f"[four_meme] 税率解析失败(跳过): {e}")

        # ── 4. 持仓集中度（排除平台合约地址）──
        try:
            holders_data = await self.analyze_token_holders(
                token_address, deployer_address,
                pair_address=FOUR_MEME_CONTRACT  # 排除 bonding curve 合约
            )
            if holders_data:
                max_share = holders_data.get("max_single_share", 0)
                top_5 = holders_data.get("top_5_share", 0)
                # 新币早期单一持仓 >15% 才算风险（宽松于普通代币的10%）
                if max_share > 15:
                    deduct = -20
                    score += deduct
                    risk_items.append({"desc": f"单一巨鲸持仓 {max_share:.1f}%", "score": deduct})
                if top_5 > 50:
                    deduct = -10
                    score += deduct
                    risk_items.append({"desc": f"前5持仓集中 {top_5:.1f}%", "score": deduct})
                result["raw_data"]["holders"] = {"max_single_share": max_share, "top_5_share": top_5}
        except Exception as e:
            logger.warning(f"[four_meme] 持仓分析失败(跳过): {e}")

        # ── 5. 加分项：社交媒体 ──
        try:
            # four.meme 合约基本都未开源，跳过 BscScan 获取，直接检查 GoPlus 里的信息
            # 如果 GoPlus 返回了 token 信息说明已有基础数据
            if goplus_res.get("token_name") or goplus_res.get("token_symbol"):
                score += 5
                bonus_items.append({"desc": "GoPlus 已收录代币信息", "score": +5})
        except Exception as e:
            logger.warning(f"[four_meme] 加分项检查失败(跳过): {e}")

        # ── 6. 评分区间限制 ──
        score = max(0, min(100, score))

        # ── 7. 决策 ──
        if score >= 75:
            decision = "buy"
        elif score >= 60:
            decision = "half_buy"
        else:
            decision = "reject"

        result["final_score"] = score
        result["decision"] = decision
        result["risk_items"] = risk_items
        result["bonus_items"] = bonus_items
        result["analysis_time"] = time.time() - start_time

        logger.info(f"[four_meme] 分析完成: {token_address[:10]}... Score={score} Decision={decision}"
                    + (f" 风险: {[r['desc'] for r in risk_items]}" if risk_items else ""))
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

        # ── 全并行：本地检测 + 所有外部API同时发起 ──
        # 检测链路: GMGN API (最快感知) → 本地模拟 → GoPlus API / Honeypot.is API (并行)
        # 任意一个确认貔貅即硬拒绝。
        raw_results = await asyncio.gather(
            _timed(self._task_local_checks(token_address, deployer_address, pair_address), "local_checks"),
            _timed(self.check_goplus(token_address),                                        "goplus"),
            _timed(self.check_honeypot_is(token_address),                                   "honeypot"),
            _timed(self.check_gmgn(token_address),                                          "gmgn"),
            _timed(self.check_contract_code(token_address),                                 "contract"),
            _timed(self.analyze_token_holders(token_address, deployer_address, pair_address), "holders"),
            _timed(self.analyze_deployer_history(deployer_address),                         "deployer"),
            _timed(self.check_bytecode_similarity(token_address),                           "similarity"),
            _timed(self.analyze_buyer_fund_source(pair_address, token_address),             "fund_source"),
            _timed(self.check_deployer_token_retention(token_address, deployer_address),    "retention"),
            _timed(self.analyze_observation(token_address, pair_address, initial_state),    "observation"),
            _timed(asyncio.wait_for(self.check_gmgn_token_stat(token_address), timeout=3.0),  "gmgn_stat"),
            _timed(asyncio.wait_for(self.check_price_behavior(token_address),  timeout=3.0),  "price_behavior"),
            _timed(asyncio.wait_for(self.check_holder_structure(token_address), timeout=3.0), "holder_struct"),
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
        gmgn_data         = _unwrap(raw_results[3], {})
        contract_data     = _unwrap(raw_results[4], {})
        holders_data      = _unwrap(raw_results[5], {})
        deployer_data     = _unwrap(raw_results[6], {})
        similarity_reason = _unwrap(raw_results[7], None)
        fund_source_data  = _unwrap(raw_results[8], {})
        retention_data    = _unwrap(raw_results[9], {})
        observation_data  = _unwrap(raw_results[10], {})
        gmgn_stat_data    = _unwrap(raw_results[11], {})
        price_beh_data    = _unwrap(raw_results[12], {})
        holder_stru_data  = _unwrap(raw_results[13], {})

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
                logger.warning(f"GoPlus 确认貔貅，硬拒绝: {token_address[:10]}")
                return self._finalize_result(result, score, risk_items, bonus_items, start_time)

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

        # 1b. GMGN 检测（独立貔貅数据库）
        result["raw_data"]["gmgn"] = gmgn_data
        if gmgn_data:
            if gmgn_data.get("is_honeypot") is True:
                score = 0
                risk_items.append({"desc": "GMGN 确认貔貅", "score": -100})
                logger.warning(f"GMGN 确认貔貅，硬拒绝: {token_address[:10]}")
                return self._finalize_result(result, score, risk_items, bonus_items, start_time)
            rug_ratio = float(gmgn_data.get("rug_ratio", 0) or 0)
            if rug_ratio >= 0.8:
                score = 0
                risk_items.append({"desc": f"GMGN: 高Rug概率 ({rug_ratio*100:.0f}%)", "score": -100})
                logger.warning(f"GMGN 高Rug概率 {rug_ratio:.2f} 硬拒绝: {token_address[:10]}")
                return self._finalize_result(result, score, risk_items, bonus_items, start_time)
            if gmgn_data.get("is_blacklisted") is True:
                score = 0
                risk_items.append({"desc": "GMGN: 代币已被黑名单", "score": -100})
                logger.warning(f"GMGN 黑名单硬拒绝: {token_address[:10]}")
                return self._finalize_result(result, score, risk_items, bonus_items, start_time)
            top10_rate = float(gmgn_data.get("top_10_holder_rate", 0) or 0)
            if top10_rate >= 0.95:
                score = 0
                risk_items.append({"desc": f"GMGN: 前10持仓过度集中 ({top10_rate*100:.0f}%)", "score": -100})
                logger.warning(f"GMGN 持仓过度集中 {top10_rate:.2f} 硬拒绝: {token_address[:10]}")
                return self._finalize_result(result, score, risk_items, bonus_items, start_time)
            if gmgn_data.get("renounced") is False:
                score -= 10
                risk_items.append({"desc": "GMGN: 合约未放弃所有权", "score": -10})
            if gmgn_data.get("low_liquidity") is True:
                score -= 15
                risk_items.append({"desc": "GMGN: 流动性不足", "score": -15})

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

        # 11. GMGN Token Stat 过滤（新增）
        if gmgn_stat_data:
            _t10  = float(gmgn_stat_data.get("top_10_holder_rate", 0) or 0)
            _hcnt = int(gmgn_stat_data.get("holder_count", 0) or 0)
            _rat  = float(gmgn_stat_data.get("rat_trader_amount_percentage", 0) or 0)
            _dev  = float(gmgn_stat_data.get("creator_hold_rate", 0) or 0)
            _age_min = price_beh_data.get("token_age_minutes") if price_beh_data else None
            _liq  = price_beh_data.get("liquidity_usd", 0) if price_beh_data else 0

            if _t10 >= 0.50:
                _snap = {"top10_rate": round(_t10, 4), "holder_count": _hcnt, "liquidity_usd": _liq}
                self._log_rejection(token_address, f"Top10持仓超50% ({_t10*100:.0f}%)", _snap)
                score = 0
                risk_items.append({"desc": f"Top10持仓超50%，筹码高度集中 ({_t10*100:.0f}%)", "score": -100})
                return self._finalize_result(result, score, risk_items, bonus_items, start_time)

            if _rat >= 0.30:
                _snap = {"rat_trader_ratio": round(_rat, 4), "top10_rate": round(_t10, 4), "holder_count": _hcnt}
                self._log_rejection(token_address, f"老鼠仓占比超30% ({_rat*100:.0f}%)", _snap)
                score = 0
                risk_items.append({"desc": f"老鼠仓占比超30% ({_rat*100:.0f}%)", "score": -100})
                return self._finalize_result(result, score, risk_items, bonus_items, start_time)

            if 0 < _hcnt <= 50:
                _snap = {"holder_count": _hcnt, "top10_rate": round(_t10, 4), "liquidity_usd": _liq}
                self._log_rejection(token_address, f"持有者数量不足50人 ({_hcnt})", _snap)
                score = 0
                risk_items.append({"desc": f"持有者不足50人 ({_hcnt})", "score": -100})
                return self._finalize_result(result, score, risk_items, bonus_items, start_time)

            if _dev < 0.001 and _age_min is not None and 0 < _age_min < 60:
                _snap = {"dev_holding_pct": round(_dev, 6), "token_age_min": round(_age_min, 1)}
                self._log_rejection(token_address, f"上线{_age_min:.0f}分钟内DEV已清仓", _snap)
                score = 0
                risk_items.append({"desc": f"上线60分钟内DEV已清仓（{_age_min:.0f}分钟）", "score": -100})
                return self._finalize_result(result, score, risk_items, bonus_items, start_time)

        # 12. 价格行为过滤（新增）
        if price_beh_data and price_beh_data.get("reject"):
            _reason = price_beh_data.get("reason", "价格异常")
            _snap = {
                "price_drop_pct": price_beh_data.get("price_change_24h", 0),
                "price_change_1h": price_beh_data.get("price_change_1h", 0),
                "price_change_5m": price_beh_data.get("price_change_5m", 0),
                "liquidity_usd": price_beh_data.get("liquidity_usd", 0),
                "token_age_min": price_beh_data.get("token_age_minutes"),
            }
            self._log_rejection(token_address, _reason, _snap)
            score = 0
            risk_items.append({"desc": _reason, "score": -100})
            return self._finalize_result(result, score, risk_items, bonus_items, start_time)

        # 动态流动性门槛
        if price_beh_data:
            _liq_usd = float(price_beh_data.get("liquidity_usd", 0) or 0)
            _age_min = price_beh_data.get("token_age_minutes")
            _min_liq = self._get_min_liquidity_threshold(_age_min)
            if 0 < _liq_usd < _min_liq:
                _snap = {"liquidity_usd": round(_liq_usd, 2), "min_threshold": _min_liq, "token_age_min": round(_age_min, 1) if _age_min else None}
                self._log_rejection(token_address, f"流动性低于动态门槛 (${_liq_usd:.0f} < ${_min_liq:.0f})", _snap)
                score = 0
                risk_items.append({"desc": f"新代币流动性不足动态门槛 (${_liq_usd:.0f})", "score": -100})
                return self._finalize_result(result, score, risk_items, bonus_items, start_time)

        # 13. 持仓结构过滤（新增）
        if holder_stru_data and holder_stru_data.get("reject"):
            _reason = holder_stru_data.get("reason", "持仓结构异常")
            _snap = {
                "top1_holder_rate": round(holder_stru_data.get("top1_holder_rate", 0), 4),
                "top2_holder_combined_rate": round(holder_stru_data.get("top2_holder_combined_rate", 0), 4),
                "top10_holder_rate": round(holder_stru_data.get("top10_holder_rate", 0), 4),
            }
            self._log_rejection(token_address, _reason, _snap)
            score = 0
            risk_items.append({"desc": _reason, "score": -100})
            return self._finalize_result(result, score, risk_items, bonus_items, start_time)
        if holder_stru_data and holder_stru_data.get("score_deduct"):
            score += holder_stru_data["score_deduct"]
            risk_items.append({"desc": holder_stru_data.get("deduct_reason", "持仓集中扣分"), "score": holder_stru_data["score_deduct"]})

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
