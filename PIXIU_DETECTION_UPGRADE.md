# 貔貅检测逻辑升级报告

## 问题背景

以下代币全部呈现"买入成功但卖出100%失败"的貔貅模式：
- **SUP**: `0x78b51e10b3B8defEa2c639d41...` (买入价格1513 BNB/枚)
- **MC**: `0x6B9B690D58D42aDF3CFBdb...`
- **JGGL**: `0x04039C52c9793b7393Db659b...` (买入价格1012 BNB/枚)
- **IVT**: `0x74cDa07Ade903C7fc18742ec8...`

共同特征：
1. 单枚代币价格异常高（>100 BNB/枚）或总量极少
2. BUY滑点0%成功，SELL滑点9%仍revert
3. 触发emergency_crash但卖出失败
4. 最终全部 -100%

---

## 修改内容

### 一、新增 `check_price_sanity()` - 价格异常检测

**位置**: `bsc_bot/analyzer/security_checker.py`

**检测逻辑**:

1. **硬拒绝1: decimals异常**
   - 标准为18，允许范围6-24
   - 超出范围直接拒绝

2. **硬拒绝2: 总供应量极少**
   - 阈值: < 1000枚
   - 防止人为制造稀缺感的貔貅币

3. **硬拒绝3: 市值流动性倒挂**
   - 正常代币: 流动性 >= 市值的1%
   - 貔貅币: 虚高市值但池子极小
   - 计算公式: `liq_ratio = liquidity_usd / market_cap_usd`
   - 拒绝条件: `liq_ratio < 0.01`

**返回格式**:
```python
{
    "reject": True/False,
    "reason": "拒绝原因"
}
```

---

### 二、新增 `check_sell_feasibility()` - 卖出可行性预检测

**位置**: `bsc_bot/analyzer/security_checker.py`

**检测流程**:

1. **步骤1: 模拟买入极小量**
   - 金额: 0.0001 BNB
   - 失败 → 直接拒绝

2. **步骤2: 立即模拟卖出**
   - 卖出刚买入的代币
   - revert → 确认为貔貅
   - 成功但税率>50% → 确认为貔貅

**返回格式**:
```python
{
    "reject": True/False,
    "reason": "拒绝原因"
}
```

---

### 三、新增 `simulate_buy()` 和 `simulate_sell()` 方法

**位置**: `bsc_bot/analyzer/local_simulator.py`

#### `simulate_buy(token_address, amount_bnb=0.0001)`

模拟买入操作，返回预期获得的代币数量。

**返回格式**:
```python
{
    "success": True/False,
    "received_amount": int,  # 获得的代币数量
    "status": "success"/"revert",
    "revert_reason": str  # 失败原因
}
```

#### `simulate_sell(token_address, amount_token)`

模拟卖出操作，检测是否能成功卖出。

**返回格式**:
```python
{
    "success": True/False,
    "status": "success"/"revert",
    "effective_tax": float,  # 有效税率 (0.0-1.0)
    "revert_reason": str  # 失败原因
}
```

---

### 四、集成到主分析流程

**位置**: `bsc_bot/analyzer/security_checker.py` - `analyze()` 方法

#### 1. 并行检测中新增两项

```python
raw_results = await asyncio.gather(
    # ... 原有检测 ...
    _timed(self.check_price_sanity(token_address, pair_address), "price_sanity"),
    _timed(self.check_sell_feasibility(token_address), "sell_feasibility"),
    return_exceptions=True
)
```

#### 2. 结果解包

```python
price_sanity_data = _unwrap(raw_results[14], {})
sell_feas_data    = _unwrap(raw_results[15], {})
```

#### 3. 硬拒绝判断（优先级高）

```python
# 价格异常检测硬拒绝
if price_sanity_data.get("reject"):
    score = 0
    risk_items.append({"desc": f"价格异常: {price_sanity_data.get('reason')}", "score": -100})
    self._log_rejection(token_address, price_sanity_data.get("reason"), {"price_sanity": price_sanity_data})
    return self._finalize_result(result, score, risk_items, bonus_items, start_time)

# 卖出可行性检测硬拒绝
if sell_feas_data.get("reject"):
    score = 0
    risk_items.append({"desc": f"卖出不可行: {sell_feas_data.get('reason')}", "score": -100})
    self._log_rejection(token_address, sell_feas_data.get("reason"), {"sell_feasibility": sell_feas_data})
    return self._finalize_result(result, score, risk_items, bonus_items, start_time)
```

---

## 预期效果

以下地址修复后应全部被拦截：

| 地址 | 代币 | 特征 | 预期拦截点 |
|------|------|------|-----------|
| `0x78b51e10b3B8defEa2c639d41...` | SUP | 单价1513 BNB | `check_price_sanity` (总量极少) |
| `0x6B9B690D58D42aDF3CFBdb...` | MC | SELL两次均revert | `check_sell_feasibility` (模拟卖出失败) |
| `0x04039C52c9793b7393Db659b...` | JGGL | 单价1012 BNB | `check_price_sanity` (总量极少) |
| `0x74cDa07Ade903C7fc18742ec8...` | IVT | 上次漏检 | `check_sell_feasibility` (模拟卖出失败) |

---

## 测试方法

### 方法1: 语法检查

```bash
cd D:/workSpace/tugou
python check_syntax.py
```

### 方法2: 完整测试（需要运行环境）

```bash
cd D:/workSpace/tugou
python test_pixiu_detection.py
```

### 方法3: 实盘验证

```bash
cd D:/workSpace/tugou
python bsc_bot/main.py --mode simulation
```

观察日志中是否出现：
- `价格异常: ...`
- `卖出不可行: ...`

---

## 文件修改清单

1. ✅ `bsc_bot/analyzer/local_simulator.py`
   - 新增 `simulate_buy()` 方法
   - 新增 `simulate_sell()` 方法

2. ✅ `bsc_bot/analyzer/security_checker.py`
   - 新增 `check_price_sanity()` 方法
   - 新增 `check_sell_feasibility()` 方法
   - 修改 `analyze()` 方法，集成新检测

3. ✅ `check_syntax.py` (新建)
   - 语法检查脚本

4. ✅ `test_pixiu_detection.py` (新建)
   - 貔貅检测测试脚本

---

## 注意事项

1. **性能影响**: 新增两项检测均为并行执行，不会显著增加总耗时
2. **误杀风险**:
   - 总量<1000枚的正常代币可能被误杀（极少见）
   - 可根据实际情况调整阈值
3. **RPC调用**:
   - `check_price_sanity`: 2次 multicall (decimals + totalSupply)
   - `check_sell_feasibility`: 模拟买入+卖出，约4-6次RPC调用

---

## 后续优化建议

1. **动态阈值**: 根据代币类型（meme/utility）调整检测阈值
2. **缓存优化**: 对已检测代币缓存结果，避免重复检测
3. **监控告警**: 统计被新检测拦截的代币数量，评估效果

---

**修改完成时间**: 2026-03-10
**修改人**: Claude Sonnet 4.6
