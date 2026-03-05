# BSC 自动交易机器人 (BSC Trading Bot)

这是一个基于 Python 的 BSC (Binance Smart Chain) 链上自动交易机器人，集成了新币监听、安全检测、自动买入和仓位管理功能。

## 快速开始

详细操作请参阅 [操作手册 (MANUAL.md)](MANUAL.md)。

### 启动方式
双击根目录下的 **`start_web.bat`** 即可一键启动前端和后端服务。

### 核心功能
1.  **新币监听 (Monitor)**: 实时监听 PancakeSwap V2/V3 和 Biswap 的新 Pair 创建事件。
2.  **安全分析 (Analyzer)**: 
    *   集成 GoPlus、Honeypot.is API 进行貔貅检测。
    *   分析合约源码（是否开源、含危险函数）。
    *   检查部署者历史和流动性状态。
3.  **自动交易 (Executor)**:
    *   支持自定义 Gas 策略 (Normal/Frontrun)。
    *   支持滑点控制和防夹保护。
    *   **模拟模式**: 支持模拟交易，验证策略而不消耗真实资金。
4.  **仓位管理 (Position Manager)**:
    *   自动追踪止损 (Trailing Stop)。
    *   分批止盈 (Take Profit)。
    *   时间止损 (Time Stop)。
    *   每日风控 (Daily Risk Control)。
    *   **可视化仪表盘**: 实时监控持仓盈亏和系统状态。

## 目录结构

```
tugou/
├── bsc_bot/            # Python 后端核心逻辑
│   ├── monitor/        # 监听模块
│   ├── analyzer/       # 分析模块
│   ├── executor/       # 交易与仓位管理
│   ├── config.yaml     # 核心配置文件
│   └── bot.py          # 机器人主程序
├── web/                # 后端 API 接口 (FastAPI)
├── frontend/           # 前端界面 (React + Vite)
├── start_web.bat       # 一键启动脚本
└── MANUAL.md           # 操作手册
```

## 环境配置

1.  **安装依赖**:
    ```bash
    # 后端
    pip install -r requirements.txt
    
    # 前端
    cd frontend
    npm install
    ```

2.  **配置环境变量 (.env)**:
    在 `bsc_bot` 目录下创建 `.env` 文件（参考示例）。


    ```ini
    # BSC 节点 (建议使用付费节点以保证速度，如 QuickNode/Alchemy)
    BSC_WS_RPC=wss://bsc-mainnet.nodereal.io/ws/v1/your_api_key
    
    # 钱包私钥 (用于交易，测试模式下可不填或随意填)
    WALLET_PRIVATE_KEY=your_private_key_here
    
    # BscScan API Key (用于获取合约源码)
    BSCSCAN_API_KEY=your_bscscan_api_key
    
    # Telegram 通知 (可选)
    TELEGRAM_BOT_TOKEN=your_bot_token
    TELEGRAM_CHAT_ID=your_chat_id
    ```

    **配置项说明**:
    *   `BSC_WS_RPC`: WebSocket RPC 地址，必须支持 `eth_subscribe`。
    *   `WALLET_PRIVATE_KEY`: 你的钱包私钥。**请务必保管好私钥，不要泄露给任何人！**
    *   `BSCSCAN_API_KEY`: 在 [BscScan](https://bscscan.com/myapikey) 申请的 API Key。

3.  **修改配置文件 (config.yaml)**:
    根据需要调整 `bsc_bot/config.yaml` 中的参数，例如买入金额、止盈止损设置等。

## 启动指南

### 1. 启动方式 (推荐)
直接运行 `start_web.bat`，系统会自动启动后端 API 和前端界面，并打开浏览器。

### 2. 运行模式设置
修改 `bsc_bot/config.yaml` 文件：

```yaml
# 模拟模式 (推荐用于测试)
mode: simulation

# 实盘模式 (请确保 .env 中配置了私钥)
# mode: live
```

## 查看日志

*   **界面查看**: 在仪表盘的“系统日志”页面实时查看。
*   **文件查看**: 日志文件保存在 `bsc_bot/logs/bot.log`。

## 常见问题 (FAQ)

**Q: 打开页面白屏?**
A: 请等待 10-20 秒让后端初始化，然后刷新页面。确保访问的是 `http://localhost:3000`。

**Q: 报错 `ConnectionError: RPC 连接失败`?**
A: 请检查 `bsc_bot/config.yaml` 中的节点配置，建议使用稳定的付费节点。

**Q: 模拟数据不更新?**
A: 确认 `mode: simulation` 已开启，且机器人已成功启动（查看日志）。


**Q: 为什么没有买入?**
A: 可能是安全评分未达到阈值（默认 80 分），或者流动性过低。查看日志中的 "Security Score" 和 "Decision"。

**Q: 数据库在哪里?**
A: 数据存储在 `data/bsc_bot.db` (SQLite)。可以使用 DB Browser for SQLite 查看。

## 安全建议

*   **私钥安全**: 永远不要将私钥上传到 GitHub 或发送给他人。
*   **资金隔离**: 建议使用专用的钱包运行机器人，不要存放大量资金。
*   **代码审计**: 建议自行审查 `analyzer/security_checker.py` 和 `executor/trader.py` 的逻辑。
