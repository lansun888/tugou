import aiohttp
import yaml
import os

# Try to find config.yaml
CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")

config = {}
try:
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
except Exception as e:
    print(f"Failed to load config from {CONFIG_PATH}: {e}")

# Check proxy config
PROXY_HTTP = None
proxy_config = config.get('proxy', {})
if proxy_config.get('enabled'):
    PROXY_HTTP = proxy_config.get('http')

# 持久化 session，避免每次请求重建 TCP 连接
# trust_env=False：不读系统 HTTP_PROXY，完全由 proxy 参数决定
_proxy_session: aiohttp.ClientSession = None
_direct_session: aiohttp.ClientSession = None


def _get_proxy_session() -> aiohttp.ClientSession:
    global _proxy_session
    if _proxy_session is None or _proxy_session.closed:
        _proxy_session = aiohttp.ClientSession(
            trust_env=False,
            connector=aiohttp.TCPConnector(limit=10, ttl_dns_cache=300),
        )
    return _proxy_session


def _get_direct_session() -> aiohttp.ClientSession:
    global _direct_session
    if _direct_session is None or _direct_session.closed:
        _direct_session = aiohttp.ClientSession(
            trust_env=False,
            connector=aiohttp.TCPConnector(limit=10, ttl_dns_cache=300),
        )
    return _direct_session


async def fetch_with_proxy(url: str, timeout: int = 3, params: dict = None) -> dict:
    """需要代理的请求（DexScreener 等境外 API）。timeout 默认 3s。"""
    try:
        session = _get_proxy_session()
        async with session.get(
            url,
            proxy=PROXY_HTTP,
            timeout=aiohttp.ClientTimeout(total=timeout),
            params=params,
        ) as resp:
            if resp.status != 200:
                return {}
            return await resp.json()
    except Exception as e:
        print(f"fetch_with_proxy error for {url}: {e}")
        return {}


async def fetch_direct(url: str, timeout: int = 5, params: dict = None) -> dict:
    """直连请求（BSCScan、GoPlus 等）。"""
    try:
        session = _get_direct_session()
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=timeout),
            params=params,
        ) as resp:
            if resp.status != 200:
                return {}
            return await resp.json()
    except Exception as e:
        print(f"fetch_direct error for {url}: {e}")
        return {}
