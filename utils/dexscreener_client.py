
import aiohttp
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 模块级持久 session，避免每次请求重建连接；trust_env=False 绕过系统代理
_session: Optional[aiohttp.ClientSession] = None


def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        connector = aiohttp.TCPConnector(limit=20)
        _session = aiohttp.ClientSession(
            connector=connector,
            trust_env=False,  # 不读取 http_proxy / https_proxy 环境变量
            timeout=aiohttp.ClientTimeout(total=10),
        )
    return _session


async def get_token_data(token_address: str) -> Optional[Dict]:
    """
    Fetch single token data from DexScreener.
    """
    url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
    try:
        session = _get_session()
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.json()
                pairs = data.get('pairs', [])
                if not pairs:
                    return None
                bsc_pair = next((p for p in pairs if p.get('chainId') == 'bsc'), pairs[0])
                return _parse_pair_data(bsc_pair)
    except Exception as e:
        logger.warning(f"DexScreener fetch failed for {token_address}: {e}")
    return None


async def get_batch_prices(token_addresses: List[str]) -> Dict[str, Dict]:
    """
    Fetch multiple tokens data from DexScreener.
    Returns a dict: {token_address_lower: parsed_data}
    """
    if not token_addresses:
        return {}

    chunk_size = 30
    results = {}
    unique_addrs = list(set(addr.lower() for addr in token_addresses))

    for i in range(0, len(unique_addrs), chunk_size):
        chunk = unique_addrs[i:i + chunk_size]
        addresses_str = ",".join(chunk)
        url = f"https://api.dexscreener.com/latest/dex/tokens/{addresses_str}"

        try:
            session = _get_session()
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    pairs = data.get('pairs', [])
                    if not pairs:
                        continue

                    for pair in pairs:
                        if pair.get('chainId') != 'bsc':
                            continue
                        base_token = pair.get('baseToken', {})
                        addr = base_token.get('address', '').lower()
                        if addr and addr in chunk:
                            if addr not in results:
                                results[addr] = _parse_pair_data(pair)
                            else:
                                current_liq = results[addr].get('liquidity_usd', 0)
                                new_liq = float(pair.get('liquidity', {}).get('usd', 0))
                                if new_liq > current_liq:
                                    results[addr] = _parse_pair_data(pair)
        except Exception as e:
            logger.warning(f"DexScreener batch fetch failed: {e}")

    return results


def _parse_pair_data(pair: Dict) -> Dict:
    """Helper to parse pair data into standardized format"""
    price_native = float(pair.get('priceNative', 0))
    price_usd = float(pair.get('priceUsd', 0))

    liquidity = pair.get('liquidity', {})
    liquidity_usd = float(liquidity.get('usd', 0))
    bnb_price = price_usd / price_native if price_native > 0 else 0
    liquidity_bnb = liquidity_usd / bnb_price if bnb_price > 0 else 0

    volume = pair.get('volume', {})
    volume_24h = float(volume.get('h24', 0))

    price_change = pair.get('priceChange', {})
    price_change_5m = float(price_change.get('m5', 0))

    txns = pair.get('txns', {})
    txns_m5 = txns.get('m5', {})
    txns_5m_buys = int(txns_m5.get('buys', 0))
    txns_5m_sells = int(txns_m5.get('sells', 0))

    market_cap = float(pair.get('fdv', 0))

    return {
        'price_bnb': price_native,
        'price_usd': price_usd,
        'liquidity_bnb': liquidity_bnb,
        'liquidity_usd': liquidity_usd,
        'volume_24h': volume_24h,
        'price_change_5m': price_change_5m,
        'market_cap': market_cap,
        'txns_5m_buys': txns_5m_buys,
        'txns_5m_sells': txns_5m_sells,
        'source': 'dexscreener',
        'pair_address': pair.get('pairAddress'),
        'url': pair.get('url')
    }
