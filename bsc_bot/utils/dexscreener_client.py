import aiohttp
import asyncio
import time
from .http_client import fetch_with_proxy

DEXSCREENER_API = "https://api.dexscreener.com"

# ── 熔断器：连续失败 3 次后暂停 30 秒，避免代理挂掉时无效刷请求 ──
# TCP RST（连接被远端强制断开）和超时都会触发失败计数
# 冷却时间缩短为 30s（原60s），代理恢复后尽快恢复服务
_CB_THRESHOLD = 3       # 连续失败几次触发熔断
_CB_COOLDOWN  = 30.0    # 熔断冷却时间（秒），缩短以加快恢复
_cb_fail_count: int   = 0
_cb_open_until: float = 0.0


def _cb_record_failure():
    global _cb_fail_count, _cb_open_until
    _cb_fail_count += 1
    if _cb_fail_count >= _CB_THRESHOLD:
        _cb_open_until = time.time() + _CB_COOLDOWN
        _cb_fail_count = 0  # 重置计数，冷却后允许再次尝试


def _cb_record_success():
    global _cb_fail_count, _cb_open_until
    _cb_fail_count = 0
    _cb_open_until = 0.0


def _cb_is_open() -> bool:
    """熔断器打开（暂停请求）时返回 True"""
    return time.time() < _cb_open_until


def _parse_pair(pair: dict) -> dict:
    """将 DexScreener 原始 pair 对象解析为统一格式（与 get_token_data 返回格式一致）"""
    vol = pair.get('volume') or {}
    change = pair.get('priceChange') or {}
    liq = pair.get('liquidity') or {}
    txns_m5 = ((pair.get('txns') or {}).get('m5') or {})
    return {
        'price_usd': float(pair.get('priceUsd') or 0),
        'price_bnb': float(pair.get('priceNative') or 0),
        'liquidity_usd': float(liq.get('usd') or 0),
        'liquidity_bnb': float(liq.get('quote') or 0),
        'market_cap': float(pair.get('marketCap') or 0),
        'fdv': float(pair.get('fdv') or 0),
        'volume_24h': float(vol.get('h24') or 0),
        'price_change_5m': float(change.get('m5') or 0),
        'price_change_1h': float(change.get('h1') or 0),
        'pair_address': pair.get('pairAddress'),
        'dex_id': pair.get('dexId'),
        'txns_5m_buys': int(txns_m5.get('buys') or 0),
        'txns_5m_sells': int(txns_m5.get('sells') or 0),
    }


async def get_token_data(token_address: str) -> dict:
    """
    通过代币地址获取完整市场数据
    接口：GET /token-pairs/v1/bsc/{tokenAddress}
    返回流动性最大的交易对数据
    """
    if _cb_is_open():
        return None  # 熔断中，跳过请求

    url = f"{DEXSCREENER_API}/token-pairs/v1/bsc/{token_address}"
    try:
        pairs = await fetch_with_proxy(url, timeout=3)
        if not pairs:
            _cb_record_failure()
            return None

        if isinstance(pairs, list):
            pair_list = pairs
        elif isinstance(pairs, dict):
            pair_list = pairs.get('pairs', [])
        else:
            _cb_record_failure()
            return None

        if not pair_list:
            # 空列表不算失败（代币未上 DexScreener），不计入熔断
            return None

        _cb_record_success()
        best_pair = max(pair_list, key=lambda x: (x.get('liquidity') or {}).get('usd', 0))
        return _parse_pair(best_pair)
    except Exception as e:
        _cb_record_failure()
        return None


async def get_batch_prices(token_addresses: list) -> dict:
    """
    批量查询多个代币价格（最多30个）
    接口：GET /tokens/v1/bsc/{address1},{address2},...
    返回格式与 get_token_data 一致（已解析）
    """
    if not token_addresses:
        return {}

    if _cb_is_open():
        return {}  # 熔断中，整批跳过

    results = {}
    chunks = [token_addresses[i:i+30] for i in range(0, len(token_addresses), 30)]

    for chunk in chunks:
        addresses = ','.join(chunk)
        url = f"{DEXSCREENER_API}/tokens/v1/bsc/{addresses}"
        try:
            pairs_list = await fetch_with_proxy(url, timeout=8)
            if not pairs_list:
                _cb_record_failure()
                continue

            if isinstance(pairs_list, dict):
                pairs_list = pairs_list.get('pairs', [])

            if not pairs_list:
                continue  # 空列表不算失败

            _cb_record_success()
            for pair in pairs_list:
                token_addr = (pair.get('baseToken') or {}).get('address', '').lower()
                if not token_addr:
                    continue
                parsed = _parse_pair(pair)
                if token_addr not in results:
                    results[token_addr] = parsed
                else:
                    if parsed['liquidity_usd'] > results[token_addr]['liquidity_usd']:
                        results[token_addr] = parsed
        except Exception as e:
            _cb_record_failure()

    return results
