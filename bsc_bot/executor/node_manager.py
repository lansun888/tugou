import asyncio
import time
import aiohttp
from loguru import logger
from web3 import AsyncWeb3

class NodeManager:
    # 连续失败超过此次数后将节点加入黑名单
    BLACKLIST_THRESHOLD = 5
    # 黑名单节点每隔此轮次重试一次（30s/轮，即 5 分钟）
    BLACKLIST_RETRY_ROUNDS = 10
    
    # 默认稳定节点 (优先使用第三方聚合RPC，避免币安官方节点在代理下超时)
    DEFAULT_NODES = [
        "https://1rpc.io/bnb",
        "https://bsc-rpc.publicnode.com",
        "https://bsc.drpc.org",
        "https://bscrpc.com"
    ]

    def __init__(self, config):
        self.config = config
        self.nodes = []
        self.latencies = {}
        self.best_node = None
        self.running = False
        self._session: aiohttp.ClientSession = None  # 持久化 session，避免每次 ping 都创建新连接
        self._failure_counts: dict = {}   # url -> 连续失败次数
        self._blacklist: set = set()      # 暂时屏蔽的节点
        self._blacklist_rounds: dict = {} # url -> 已跳过轮次数

        # Load nodes from config
        node_config = self.config.get("network", {}).get("nodes", {})
        self.execute_nodes = node_config.get("execute", [])

        # Fallback to private_rpcs if execute nodes not found
        if not self.execute_nodes:
            self.execute_nodes = self.config.get("network", {}).get("private_rpcs", [])
            
        # Merge with default stable nodes if empty or just append to ensure coverage
        if not self.execute_nodes:
             self.execute_nodes = self.DEFAULT_NODES
        else:
             # Ensure default nodes are included for redundancy
             if isinstance(self.execute_nodes, str):
                 self.execute_nodes = [self.execute_nodes]
             for node in self.DEFAULT_NODES:
                 if node not in self.execute_nodes:
                     self.execute_nodes.append(node)

        # Ensure we have a list
        if isinstance(self.execute_nodes, str):
            self.execute_nodes = [self.execute_nodes]

        # 去重，保持顺序
        seen = set()
        deduped = []
        for url in self.execute_nodes:
            if url not in seen:
                seen.add(url)
                deduped.append(url)
        self.execute_nodes = deduped

        logger.info(f"Loaded {len(self.execute_nodes)} execution nodes for health check.")

    async def _get_session(self) -> aiohttp.ClientSession:
        """获取或创建持久化 session"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def start_monitoring(self):
        """Start the background monitoring task"""
        self.running = True
        asyncio.create_task(self._monitor_loop())

    async def stop(self):
        self.running = False
        if self._session and not self._session.closed:
            await self._session.close()

    async def _monitor_loop(self):
        while self.running:
            try:
                await self.check_nodes()
            except Exception as e:
                logger.error(f"Node health check failed: {e}")
            
            # Wait 30s before next check
            await asyncio.sleep(30)

    async def check_nodes(self):
        """Ping all nodes and update latencies，自动跳过/剔除长期失败节点"""
        # 决定本轮哪些节点需要 ping（黑名单节点周期性重试）
        nodes_to_ping = []
        for url in self.execute_nodes:
            if url in self._blacklist:
                self._blacklist_rounds[url] = self._blacklist_rounds.get(url, 0) + 1
                if self._blacklist_rounds[url] >= self.BLACKLIST_RETRY_ROUNDS:
                    # 重试机会：重置计数，本轮 ping 一次
                    self._blacklist_rounds[url] = 0
                    nodes_to_ping.append(url)
                # 否则跳过
            else:
                nodes_to_ping.append(url)

        if not nodes_to_ping:
            logger.warning("All nodes blacklisted, forcing retry of first node")
            nodes_to_ping = self.execute_nodes[:1]

        results = await asyncio.gather(*[self._ping_node(url) for url in nodes_to_ping])

        valid_nodes = []
        for url, latency in results:
            if latency is not None:
                self.latencies[url] = latency
                valid_nodes.append((url, latency))
                # 成功：重置失败计数，从黑名单移除
                self._failure_counts[url] = 0
                self._blacklist.discard(url)
            else:
                self.latencies[url] = 9999
                count = self._failure_counts.get(url, 0) + 1
                self._failure_counts[url] = count
                if count >= self.BLACKLIST_THRESHOLD and url not in self._blacklist:
                    logger.warning(
                        f"Node {url} failed {count} times consecutively, blacklisting "
                        f"(will retry every {self.BLACKLIST_RETRY_ROUNDS * 30}s)"
                    )
                    self._blacklist.add(url)
                    self._blacklist_rounds[url] = 0

        valid_nodes.sort(key=lambda x: x[1])

        if valid_nodes:
            best_url, best_latency = valid_nodes[0]
            if self.best_node != best_url:
                logger.info(f"Switched to faster node: {best_url} ({best_latency:.2f}ms)")
                self.best_node = best_url
            else:
                logger.debug(f"Current best node: {best_url} ({best_latency:.2f}ms)")
        else:
            logger.warning("No valid execution nodes found!")

    async def _ping_node(self, url):
        """Measure latency for a simple RPC call（复用持久化 session，减少连接开销）"""
        try:
            session = await self._get_session()
            start = time.time()
            payload = {"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1}
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as response:
                if response.status == 200:
                    latency = (time.time() - start) * 1000
                    return url, latency
        except Exception:
            pass
        return url, None

    def get_best_node(self):
        """Get the current best node URL"""
        if self.best_node:
            return self.best_node
        # Fallback to first in list if check hasn't run yet
        return self.execute_nodes[0] if self.execute_nodes else None

    def get_top_nodes(self, n: int = 3) -> list:
        """按延迟返回前 N 个可用节点（用于竞速广播）"""
        available = [u for u in self.execute_nodes if u not in self._blacklist]
        if not available:
            available = self.execute_nodes[:1]
        # 有延迟数据的节点按延迟升序排列，无数据的排后面
        scored = sorted(available, key=lambda u: self.latencies.get(u, float('inf')))
        return scored[:n]
