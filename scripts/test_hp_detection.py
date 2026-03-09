KNOWN_HP_BUY_GAS  = {"154480"}
KNOWN_HP_SELL_GAS = {"107848"}

def finalize(score, risk_items):
    score = max(0, min(100, score))
    if score >= 80:   decision = "buy"
    elif score >= 60: decision = "half_buy"
    else:             decision = "reject"
    return {"final_score": score, "decision": decision, "risk_items": risk_items}

def run_checks(goplus_data, honeypot_data):
    score = 100
    risk_items = []

    if goplus_data:
        creator_pct = float(goplus_data.get("creator_percent", 0) or 0)
        if creator_pct >= 0.99:
            score = 0
            risk_items.append({"desc": f"creator持有{creator_pct*100:.0f}%", "score": -100})
            return finalize(score, risk_items)

        holder_count = int(goplus_data.get("holder_count", 99) or 99)
        holders = goplus_data.get("holders", [])
        if holder_count <= 2 and holders:
            non_lp = [h for h in holders
                      if h.get("is_contract") == 0 or float(h.get("percent", 0)) < 0.8]
            if not non_lp:
                score = 0
                risk_items.append({"desc": f"holder_count={holder_count}全为LP合约", "score": -100})
                return finalize(score, risk_items)

        if goplus_data.get("cannot_sell_all") == "1":
            score = 0
            risk_items.append({"desc": "cannot_sell_all", "score": -100})
            return finalize(score, risk_items)

        if goplus_data.get("transfer_pausable") == "1":
            score = 0
            risk_items.append({"desc": "transfer_pausable", "score": -100})
            return finalize(score, risk_items)

        if goplus_data.get("trading_cooldown") == "1":
            score -= 25
            risk_items.append({"desc": "trading_cooldown -25", "score": -25})

        if goplus_data.get("personal_slippage_modifiable") == "1":
            score -= 20
            risk_items.append({"desc": "personal_slippage -20", "score": -20})

    if honeypot_data:
        if not honeypot_data.get("simulationSuccess", False):
            score = 0
            risk_items.append({"desc": "simulationSuccess=False", "score": -100})
            return finalize(score, risk_items)

        hp_token = honeypot_data.get("token", {}) or {}
        hp_sim   = honeypot_data.get("simulationResult", {}) or {}
        if (hp_token.get("decimals") == 8
                and hp_token.get("totalSupply") is None
                and str(hp_sim.get("buyGas", "")) in KNOWN_HP_BUY_GAS
                and str(hp_sim.get("sellGas", "")) in KNOWN_HP_SELL_GAS):
            score = 0
            risk_items.append({"desc": "已知貔貅模板(decimals=8+gas匹配)", "score": -100})
            return finalize(score, risk_items)

    return finalize(score, risk_items)

# ── 测试用例: (label, expect_reject, goplus, honeypot) ──
cases = [
    # ── 应该拒绝的貔貅 ──
    ("BDAG:holder=1全LP合约", True, {
        "creator_percent": "0.000000", "holder_count": 1,
        "holders": [{"is_contract": 1, "percent": "1.000000"}],
        "cannot_sell_all": "0", "transfer_pausable": "0",
    }, {"simulationSuccess": True,
        "token": {"decimals": 18, "totalSupply": "1e9"},
        "simulationResult": {"buyGas": "200000", "sellGas": "150000"}}),

    ("PIPPKIN:creator=100%", True, {
        "creator_percent": "1.000000", "holder_count": 0, "holders": [],
        "cannot_sell_all": "0",
    }, {"simulationSuccess": True,
        "token": {"decimals": 18, "totalSupply": "1e9"},
        "simulationResult": {"buyGas": "200000", "sellGas": "150000"}}),

    ("同厂模板:decimals=8+gas=154480/107848", True, {
        "creator_percent": "0.0", "holder_count": 5,
        "holders": [{"is_contract": 0, "percent": "0.2"}],
        "cannot_sell_all": "0",
    }, {"simulationSuccess": True,
        "token": {"decimals": 8, "totalSupply": None},
        "simulationResult": {"buyGas": "154480", "sellGas": "107848"}}),

    ("simulationSuccess=False", True, {
        "creator_percent": "0.05", "holder_count": 10,
        "holders": [{"is_contract": 0, "percent": "0.1"}],
    }, {"simulationSuccess": False,
        "token": {"decimals": 18, "totalSupply": "1e9"},
        "simulationResult": {}}),

    ("cannot_sell_all=1", True, {
        "creator_percent": "0.1", "holder_count": 50,
        "holders": [{"is_contract": 0, "percent": "0.05"}],
        "cannot_sell_all": "1", "transfer_pausable": "0",
    }, {"simulationSuccess": True,
        "token": {"decimals": 18, "totalSupply": "1e9"},
        "simulationResult": {"buyGas": "200000", "sellGas": "150000"}}),

    ("transfer_pausable=1", True, {
        "creator_percent": "0.1", "holder_count": 30,
        "holders": [{"is_contract": 0, "percent": "0.05"}],
        "cannot_sell_all": "0", "transfer_pausable": "1",
    }, {"simulationSuccess": True,
        "token": {"decimals": 18, "totalSupply": "1e9"},
        "simulationResult": {}}),

    ("creator=0.995(>=0.99触发边界)", True, {
        "creator_percent": "0.995000", "holder_count": 2,
        "holders": [{"is_contract": 0, "percent": "0.005"}],
    }, {"simulationSuccess": True,
        "token": {"decimals": 18, "totalSupply": "1e9"},
        "simulationResult": {"buyGas": "200000", "sellGas": "150000"}}),

    ("trading+slippage双罚(100-25-20=55,reject)", True, {
        "creator_percent": "0.1", "holder_count": 80,
        "holders": [{"is_contract": 0, "percent": "0.1"}],
        "cannot_sell_all": "0", "transfer_pausable": "0",
        "trading_cooldown": "1", "personal_slippage_modifiable": "1",
    }, {"simulationSuccess": True,
        "token": {"decimals": 18, "totalSupply": "1e9"},
        "simulationResult": {"buyGas": "200000", "sellGas": "150000"}}),

    # ── 应该放行的正常代币 ──
    ("正常代币全绿(score=100)", False, {
        "creator_percent": "0.050000", "holder_count": 250,
        "holders": [{"is_contract": 0, "percent": "0.05"}],
        "cannot_sell_all": "0", "transfer_pausable": "0",
        "trading_cooldown": "0", "personal_slippage_modifiable": "0",
    }, {"simulationSuccess": True,
        "token": {"decimals": 18, "totalSupply": "1e9"},
        "simulationResult": {"buyGas": "180000", "sellGas": "130000"}}),

    ("decimals=8但gas不匹配(合法BTC系)", False, {
        "creator_percent": "0.05", "holder_count": 100,
        "holders": [{"is_contract": 0, "percent": "0.05"}],
        "cannot_sell_all": "0",
    }, {"simulationSuccess": True,
        "token": {"decimals": 8, "totalSupply": "21000000"},
        "simulationResult": {"buyGas": "180000", "sellGas": "125000"}}),

    ("holder=2但有真实持仓人各50%", False, {
        "creator_percent": "0.50", "holder_count": 2,
        "holders": [{"is_contract": 0, "percent": "0.50"},
                    {"is_contract": 0, "percent": "0.50"}],
        "cannot_sell_all": "0",
    }, {"simulationSuccess": True,
        "token": {"decimals": 18, "totalSupply": "1e9"},
        "simulationResult": {"buyGas": "200000", "sellGas": "150000"}}),

    ("trading_cooldown=1仅扣25分(score=75,buy)", False, {
        "creator_percent": "0.1", "holder_count": 80,
        "holders": [{"is_contract": 0, "percent": "0.1"}],
        "cannot_sell_all": "0", "transfer_pausable": "0", "trading_cooldown": "1",
    }, {"simulationSuccess": True,
        "token": {"decimals": 18, "totalSupply": "1e9"},
        "simulationResult": {"buyGas": "200000", "sellGas": "150000"}}),

    ("creator=0.98(<0.99不触发)", False, {
        "creator_percent": "0.980000", "holder_count": 5,
        "holders": [{"is_contract": 0, "percent": "0.02"}],
        "cannot_sell_all": "0",
    }, {"simulationSuccess": True,
        "token": {"decimals": 18, "totalSupply": "1e9"},
        "simulationResult": {"buyGas": "200000", "sellGas": "150000"}}),

    ("holder=1但有真实持仓(<80%是LP)", False, {
        "creator_percent": "0.05", "holder_count": 1,
        "holders": [{"is_contract": 1, "percent": "0.60"}],
        "cannot_sell_all": "0",
    }, {"simulationSuccess": True,
        "token": {"decimals": 18, "totalSupply": "1e9"},
        "simulationResult": {"buyGas": "200000", "sellGas": "150000"}}),

    ("decimals=8+gas匹配但totalSupply有值(不触发)", False, {
        "creator_percent": "0.05", "holder_count": 50,
        "holders": [{"is_contract": 0, "percent": "0.05"}],
        "cannot_sell_all": "0",
    }, {"simulationSuccess": True,
        "token": {"decimals": 8, "totalSupply": "100000000"},
        "simulationResult": {"buyGas": "154480", "sellGas": "107848"}}),
]

print(f"\n{'#':<2} {'测试用例':<40} {'期望':>8} {'实际':>8} {'分数':>5}  状态  命中规则")
print("=" * 120)

fails = 0
for i, (label, expect_reject, gp, hp) in enumerate(cases, 1):
    res = run_checks(gp, hp)
    actual_reject = res["decision"] == "reject"
    ok = (actual_reject == expect_reject)
    if not ok:
        fails += 1
    status   = "OK" if ok else "FAIL"
    actual_s = "REJECT" if actual_reject else "PASS"
    expect_s = "REJECT" if expect_reject else "PASS"
    rules    = " | ".join(r["desc"] for r in res["risk_items"])[:58]
    flag     = "✅" if ok else "❌"
    print(f"{i:<2} {label:<40} {expect_s:>8} {actual_s:>8} {res['final_score']:>5}  {flag}{status}  {rules}")

print("=" * 120)
total = len(cases)
print(f"\n结果: {total - fails}/{total} 通过  {'全部正确' if fails == 0 else f'{fails} 个失败'}")
