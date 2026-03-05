# BSC TuGou Bot 操作手册

## 1. 项目简介
BSC TuGou Bot 是一个基于币安智能链 (BSC) 的自动化交易机器人，支持新币监控、自动狙击、持仓管理和模拟/实盘交易功能。系统包含 Python 后端（负责链上交互和策略执行）和 React 前端（提供可视化仪表盘）。

## 2. 快速启动

### 2.1 环境要求
- **操作系统**: Windows (推荐)
- **依赖环境**: 
  - Python 3.10+
  - Node.js 16+

### 2.2 启动步骤
系统提供了一键启动脚本，无需手动分别启动前后端。

1. 进入项目根目录 `d:\workSpace\tugou`
2. 双击运行 **`start_web.bat`**
3. 脚本会自动执行以下操作：
   - 启动后端 API 服务 (端口 8002)
   - 启动前端开发服务器 (端口 3000)
   - 自动打开浏览器访问 `http://localhost:3000`

**注意**: 启动过程中会弹出两个命令行窗口，**请勿关闭**，否则服务会停止。

### 2.3 停止服务
要完全停止系统，请执行以下操作之一：
- 关闭所有弹出的命令行窗口
- 在根目录运行 `stop.bat` (如果有) 或在终端执行 `taskkill /F /IM python.exe` 和 `taskkill /F /IM node.exe`

## 3. 系统配置

核心配置文件位于 `bsc_bot/config.yaml`。修改配置后需要重启后端服务生效。

### 3.1 运行模式 (Mode)
```yaml
mode: simulation  # 模拟模式
# mode: live      # 实盘模式 (请谨慎开启)
```
- **simulation**: 模拟交易，不消耗真实 BNB，用于测试策略。
- **live**: 实盘交易，会使用钱包中的真实资金。

### 3.2 交易设置 (Trading)
```yaml
trading:
  buy_amount: 0.01      # 每次买入金额 (BNB)
  gas:
    price_gwei: 5       # Gas 价格
    slippage_percent: 10 # 滑点设置
```

### 3.3 监控设置 (Monitor)
```yaml
monitor:
  min_liquidity_bnb: 10.0  # 最小流动性要求
  min_security_score: 85   # 最小安全分 (0-100)
  check_honeypot: true     # 检查是否为貔貅盘
```

### 3.4 止盈止损 (Position Management)
```yaml
position_management:
  trailing_stop:           # 移动止损
    initial_stop_loss: 20  # 初始止损 (%)
  take_profit:             # 分批止盈
    levels:
      - [100, 25]          # 涨幅 100% 时卖出 25%
```

## 4. 功能模块说明

### 4.1 仪表盘 (Dashboard)
- **实盘概览**: 显示 BNB 余额、今日盈亏、当前持仓数和今日胜率。
- **模拟统计**: 显示模拟交易的累计数据和收益曲线。
- **盈亏趋势**: 过去 7 天的资金变化图表。

### 4.2 新币发现 (Discoveries)
- 实时显示链上监控到的新币。
- 展示代币安全评分、流动性信息。
- 支持手动点击“跟单”或“买入”。

### 4.3 持仓管理 (Positions)
- 查看当前所有持仓代币。
- 实时显示当前价格、未实现盈亏。
- 支持手动“一键清仓”或“卖出”特定代币。

### 4.4 交易记录 (Trades)
- 历史买卖记录查询。
- 包含交易哈希、价格、数量和最终盈亏。

### 4.5 系统日志 (Logs)
- 实时查看机器人运行日志。
- 监控报错信息和交易执行状态。

## 5. 常见问题与故障排查

### 5.1 打开页面白屏
- **原因**: 可能是后端服务未完全启动或数据加载异常。
- **解决**: 
  1. 等待 10-20 秒，后端初始化需要时间。
  2. 刷新浏览器页面。
  3. 检查后端命令行窗口是否有报错。
  4. 确保访问地址为 `http://localhost:3000` 或本机局域网 IP `http://192.168.x.x:3000`。

### 5.2 菜单无数据
- **原因**: 数据库连接失败或后端进程卡死。
- **解决**: 
  1. 关闭所有 Python 进程 (`taskkill /F /IM python.exe`)。
  2. 重新运行 `start_web.bat`。
  3. 检查 `bsc_bot/logs/bot.log` 日志文件。

### 5.3 端口被占用
- **提示**: `Error: listen EADDRINUSE: address already in use`
- **解决**: 手动结束占用端口的进程，或重启电脑。

### 5.4 模拟数据不更新
- **原因**: 模拟交易尚未触发或未开启模拟模式。
- **解决**: 检查 `config.yaml` 中 `mode` 是否为 `simulation`，并确认日志中有 "Simulation Manager initialized" 字样。
