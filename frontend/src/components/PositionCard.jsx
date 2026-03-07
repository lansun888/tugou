import React, { useState, useRef } from 'react';
import { Card, Text, Flex, Badge, Button, ProgressBar } from "@tremor/react";
import { Copy, Check, Clock, AlertTriangle, Rocket, DollarSign, Pencil, X } from 'lucide-react';
import { formatPrice } from '../utils/formatters';
import api from '../utils/api';
import GmgnLink from './common/GmgnLink';

// 持仓时长（不含"前"，简洁格式）
const formatHoldDuration = (buyTime) => {
  if (!buyTime) return '--';
  const ts = Number(buyTime);
  if (!ts || ts <= 0) return '--';
  // Unix 秒时间戳 (< 1e11 s ≈ 5138年) 转 ms
  const past = ts < 100000000000 ? new Date(ts * 1000) : new Date(ts);
  if (isNaN(past.getTime()) || past.getFullYear() < 2020) return '--';
  const diffMs = Date.now() - past.getTime();
  if (diffMs < 0) return '< 1m';   // 服务器/客户端时钟偏差
  const diffSecs = Math.floor(diffMs / 1000);
  const diffMins = Math.floor(diffSecs / 60);
  const diffHrs = Math.floor(diffMins / 60);
  if (diffHrs >= 24) return `${Math.floor(diffHrs / 24)}天${diffHrs % 24}h`;
  if (diffHrs > 0) return `${diffHrs}h${diffMins % 60}m`;
  if (diffMins > 0) return `${diffMins}m`;
  return '< 1m';   // 不足1分钟，显示 < 1m 而非 0m
};

// 取代币名/符号的首个 ASCII 大写字母，避免中文字符撑破圆形头像
const getTokenInitial = (symbol, name) => {
  const str = symbol || name || '';
  
  // FIX: Remove Emojis (Expanded range)
  const cleaned = str.replace(
    /([\u2700-\u27BF]|[\uE000-\uF8FF]|\uD83C[\uDC00-\uDFFF]|\uD83D[\uDC00-\uDFFF]|[\u2011-\u26FF]|\uD83E[\uDD10-\uDDFF])/g, 
    ''
  ).trim();
  
  // Use first valid char
  if (cleaned.length > 0) {
      // Find first alphanumeric or CJK
      for (const ch of cleaned) {
          if (/[A-Za-z0-9\u4e00-\u9fa5]/.test(ch)) return ch.toUpperCase();
      }
      return cleaned.charAt(0).toUpperCase();
  }
  
  // Fallback to raw first char if everything was stripped
  return str.charAt(0);
};

// 可内联编辑的价格组件
const EditablePrice = ({ value, fieldName, tokenAddress, color = 'text-slate-300' }) => {
  const [editing, setEditing] = useState(false);
  const [inputVal, setInputVal] = useState('');
  const inputRef = useRef(null);

  const startEdit = () => {
    setInputVal(value != null ? String(value) : '');
    setEditing(true);
    setTimeout(() => inputRef.current?.select(), 0);
  };

  const cancel = (e) => {
    e?.stopPropagation();
    setEditing(false);
  };

  const save = async () => {
    const parsed = parseFloat(inputVal);
    if (isNaN(parsed) || parsed <= 0) { setEditing(false); return; }
    try {
      await api.patch(`/positions/${tokenAddress}`, { [fieldName]: parsed });
    } catch (e) {
      console.error('Update position param failed:', e);
    }
    setEditing(false);
  };

  if (editing) {
    return (
      <div className="flex items-center gap-1" onClick={e => e.stopPropagation()}>
        <input
          ref={inputRef}
          className="w-28 text-xs bg-slate-800 text-white border border-indigo-500 rounded px-1.5 py-0.5 font-mono outline-none"
          value={inputVal}
          onChange={e => setInputVal(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') save(); if (e.key === 'Escape') cancel(); }}
          onBlur={save}
          placeholder="输入新价格"
        />
        <X className="w-3 h-3 text-slate-500 cursor-pointer flex-shrink-0" onClick={cancel} />
      </div>
    );
  }

  return (
    <div className={`flex items-center gap-1 group/edit cursor-pointer select-none ${color}`} onClick={startEdit} title="点击编辑">
      <span className="font-mono text-xs">{formatPrice(value)}</span>
      <Pencil className="w-2.5 h-2.5 text-slate-600 opacity-0 group-hover/edit:opacity-100 transition-opacity flex-shrink-0" />
    </div>
  );
};

const PositionCard = ({ pos, onSell }) => {
  const [imageError, setImageError] = useState(false);
  const [isCopied, setIsCopied] = useState(false);

  const handleCopy = (e) => {
    e.stopPropagation();
    if (pos.token_address) {
      navigator.clipboard.writeText(pos.token_address);
      setIsCopied(true);
      setTimeout(() => setIsCopied(false), 2000);
    }
  };
  
  // FIX: Recalculate PnL and Values dynamically based on current price
  // This ensures frontend is always consistent even if backend pnl_percentage is stale
  const currentPrice = pos.current_price_bnb || 0;
  const buyPrice = pos.buy_price_bnb || 0;
  
  // PnL Percentage = (Current - Buy) / Buy * 100
  // Handle division by zero
  let pnlPercentage = 0;
  if (buyPrice > 0 && currentPrice > 0) {
      pnlPercentage = ((currentPrice - buyPrice) / buyPrice) * 100;
  } else {
      // Fallback to backend value if prices are missing
      pnlPercentage = pos.pnl_percentage || 0;
  }

  // Invested Amount (Use backend value, but fallback to calculation if needed)
  const investedBnb = pos.invested_bnb || 0;
  
  // Current Value = Invested * (1 + PnL/100)
  // Or simply: Amount * CurrentPrice (if we had amount)
  // We can derive Amount = Invested / BuyPrice
  let currentValueBnb = pos.current_value_bnb || 0;
  if (investedBnb > 0 && buyPrice > 0) {
      const amount = investedBnb / buyPrice;
      currentValueBnb = amount * currentPrice;
  }
  
  // Net PnL = Current Value - Invested Value
  const netPnlBnb = currentValueBnb - investedBnb;

  const isProfitable = pnlPercentage >= 0;
  const isSuperProfitable = pnlPercentage >= 100;
  const isLoss = pnlPercentage < 0;

  // Logic: Stop Loss & Target Price Range
  const stopLossPrice = pos.stop_loss_price || 0;
  
  // FIX: Dynamic Target Price Logic (Next Take Profit Level)
  // level1: buy * 2 (+100%)
  // level2: buy * 3 (+200%)
  // level3: buy * 5 (+400%)
  // level4: buy * 10 (+900%)
  
  const getNextTarget = () => {
      // Dynamic logic as primary for "Target Price" display.
      // Even if backend has a target_price, we want to show the next dynamic level if user hasn't manually overridden it?
      // Actually, pos.target_price usually comes from config (initial) or manual update.
      // If we want "Dynamic Update", we should probably ignore the static pos.target_price unless it's a manual override.
      // However, distinguishing manual override vs initial config is hard here without extra flags.
      // User request implies: "目标价显示逻辑...应该显示还未触发的下一档止盈价格"
      // So we will prioritize the dynamic calculation.
      
      if (!buyPrice || buyPrice <= 0) return { price: pos.target_price || 0, label: '' };
      
      const levels = [2, 3, 5, 10];
      for (const multiplier of levels) {
          const t = buyPrice * multiplier;
          // Use > (greater than) to find the next target
          // Also check if current target is "close enough" to consider it "next"? 
          // No, user said: if current > target, show next.
          if (t > currentPrice) {
              return { price: t, label: `${multiplier}x目标` };
          }
      }
      // Fallback: 10x (or maybe >10x if price is huge?)
      return { price: buyPrice * 10, label: '10x目标' };
  };

  const { price: targetPrice, label: targetLabel } = getNextTarget();
  
  // 1. Status based on distance from SL
  let rawDist = 0; // % distance from SL
  if (stopLossPrice > 0 && currentPrice > 0) {
      rawDist = ((currentPrice - stopLossPrice) / stopLossPrice) * 100;
  }
  
  let statusText = '安全';
  let progressColor = 'emerald';
  let isNearStopLoss = false;
  let stopTriggered = rawDist <= 0;

  if (stopTriggered) {
      statusText = '触发止损';
      progressColor = 'rose';
      isNearStopLoss = true;
  } else if (rawDist < 10) {
      statusText = '危险';
      progressColor = 'rose';
      isNearStopLoss = true;
  } else if (rawDist < 20) {
      statusText = '注意';
      progressColor = 'yellow';
  } else {
      statusText = '安全';
      progressColor = 'emerald';
  }

  // 2. Progress Bar Value (SL -> TP Range)
  // 0% = SL, 100% = TP
  let progressValue = 0;
  if (stopLossPrice > 0 && targetPrice > stopLossPrice) {
      const totalRange = targetPrice - stopLossPrice;
      const currentPos = currentPrice - stopLossPrice;
      progressValue = (currentPos / totalRange) * 100;
  } else {
      // Fallback if no TP: use rawDist capped
      progressValue = Math.max(0, Math.min(rawDist, 100));
  }
  
  // Clamp 0-100
  progressValue = Math.max(0, Math.min(progressValue, 100));

  // 卡片边框
  let borderColorClass = "ring-slate-800";
  if (isLoss) borderColorClass = "ring-rose-900/50";
  else if (stopTriggered || isNearStopLoss) borderColorClass = "ring-amber-500/40 animate-pulse";

  const soldPercent = pos.sold_percentage || 0;
  let soldBadge = null;
  if (soldPercent >= 99) soldBadge = <Badge color="rose" size="xs">已清仓</Badge>;
  else if (soldPercent >= 50) soldBadge = <Badge color="yellow" size="xs">已卖出 {soldPercent.toFixed(0)}%</Badge>;
  else if (soldPercent >= 25) soldBadge = <Badge color="emerald" size="xs">已卖出 {soldPercent.toFixed(0)}%</Badge>;

  // 2分钟貔貅评估期标识
  const buyTs = pos.buy_time ? (pos.buy_time < 1e11 ? pos.buy_time * 1000 : pos.buy_time) : 0;
  const holdingMs = buyTs > 0 ? Date.now() - buyTs : Infinity;
  const inHoneypotCheckWindow = holdingMs < 2 * 60 * 1000; // < 2分钟

  // 代币头像：取第一个 ASCII 字符，跳过中文避免样式混乱
  const tokenInitial = getTokenInitial(pos.token_symbol, pos.token_name);
  
  // Hash for background color
  const getAvatarColor = (name) => {
      let hash = 0;
      const str = name || 'default';
      for (let i = 0; i < str.length; i++) {
          hash = str.charCodeAt(i) + ((hash << 5) - hash);
      }
      const colors = ['bg-blue-600', 'bg-indigo-600', 'bg-purple-600', 'bg-pink-600', 'bg-rose-600', 'bg-orange-600', 'bg-amber-600', 'bg-emerald-600', 'bg-teal-600', 'bg-cyan-600'];
      return colors[Math.abs(hash) % colors.length];
  };
  const avatarBg = getAvatarColor(pos.token_symbol || pos.token_name);

  // Helper for DexScreener data
  const formatDexData = (val, isCurrency = true) => {
      if (val === undefined || val === null || val === 0) return <span className="text-gray-600">-- <span className="text-[10px] opacity-60">(离线)</span></span>;
      if (isCurrency) return `$${val.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
      return val.toFixed(2) + '%';
  };
  
  const formatDexPair = (buy, sell) => {
      if ((!buy && !sell) || (buy === 0 && sell === 0)) return <span className="text-gray-600">--/--</span>;
      return (
          <div className="flex gap-1">
              <span className="text-emerald-400">{buy}</span>
              <span>/</span>
              <span className="text-rose-400">{sell}</span>
          </div>
      );
  };

  // Helper for Net PnL formatting
  const formatPnl = (bnb_amount) => {
    if (Math.abs(bnb_amount) < 0.0001) {
        return '+0.0000';
    }
    if (Math.abs(bnb_amount) < 0.01) {
        return (bnb_amount > 0 ? '+' : '') + bnb_amount.toFixed(6);
    }
    return (bnb_amount > 0 ? '+' : '') + bnb_amount.toFixed(4);
  };

  return (
    <Card className={`bg-slate-900 ring-1 ${borderColorClass} transition-all hover:ring-slate-700`}>
      {/* Header */}
      <Flex className="mb-4 border-b border-slate-800 pb-3">
        <div className="flex items-center gap-3">
          <div className={`w-9 h-9 rounded-full flex items-center justify-center text-sm font-bold text-slate-200 flex-shrink-0 overflow-hidden ${avatarBg}`}>
             {imageError ? (
               <span className="token-initial">
                 {tokenInitial}
               </span>
             ) : (
               <img 
                 src={pos.token_icon_url || ''} 
                 alt={tokenInitial}
                 className="w-full h-full object-cover"
                 onError={() => setImageError(true)} 
               />
             )}
          </div>
          <div>
            <div className="flex items-center gap-2">
              <Text className="text-white font-bold text-lg flex items-center gap-2">
                {pos.token_symbol || pos.token_name}
                {pos.dex_name === 'four_meme' && (
                  <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-bold bg-purple-100 text-purple-700 border border-purple-200" title="Four.meme Platform">4M</span>
                )}
                {isSuperProfitable && <span className="text-xs">🚀</span>}
                {inHoneypotCheckWindow && (
                  <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-900/50 text-amber-300 border border-amber-700/50" title="买入后2分钟内，正在进行延迟貔貅评估">⏳ 2min评估中</span>
                )}
                {soldBadge}
              </Text>
              <GmgnLink address={pos.token_address} />
            </div>
            <div
              className="flex items-center gap-2 cursor-pointer group select-none"
              onClick={handleCopy}
              title="点击复制地址"
            >
              <Text className="text-slate-500 text-xs font-mono group-hover:text-indigo-400 transition-colors">
                {pos.token_address.slice(0, 6)}...{pos.token_address.slice(-4)}
              </Text>
              {isCopied ? (
                <Check className="w-3 h-3 text-emerald-500" />
              ) : (
                <Copy className="w-3 h-3 text-slate-600 group-hover:text-indigo-400 transition-colors opacity-0 group-hover:opacity-100" />
              )}
              {isCopied && <span className="text-emerald-500 text-[10px]">已复制</span>}
            </div>
          </div>
        </div>

        {/* 持仓时长 */}
        <div className="text-right flex-shrink-0">
          <Text className="text-slate-500 text-[10px] uppercase tracking-wide">持仓时长</Text>
          <div className="flex items-center justify-end gap-1 mt-0.5">
            <Clock className="w-3 h-3 text-slate-500" />
            <span className="text-slate-300 text-xs font-mono">{formatHoldDuration(pos.buy_time)}</span>
          </div>
        </div>
      </Flex>

      {/* Price Grid */}
      <div className="grid grid-cols-3 gap-2 mb-4 text-center border-b border-slate-800 pb-4">
        <div className="border-r border-slate-800 pr-2">
          <Text className="text-slate-500 text-xs mb-1">买入价</Text>
          <EditablePrice
            value={pos.buy_price_bnb}
            fieldName="buy_price_bnb"
            tokenAddress={pos.token_address}
            color="text-slate-300"
          />
        </div>
        <div className="border-r border-slate-800 px-2">
          <Text className="text-slate-500 text-xs mb-1">现价</Text>
          <Text className="text-white font-mono text-sm font-medium">{formatPrice(currentPrice)}</Text>
        </div>
        <div className="pl-2">
          <Text className="text-slate-500 text-xs mb-1">盈亏</Text>
          <Text className={`font-mono text-sm font-bold ${isProfitable ? 'text-emerald-400' : 'text-rose-400'}`}>
            {isProfitable ? '+' : ''}{pnlPercentage.toFixed(1)}%
          </Text>
        </div>
      </div>

      {/* DexScreener Stats */}
      <div className="grid grid-cols-2 gap-2 mb-4 text-xs border-b border-slate-800 pb-4">
        <div className="flex flex-col gap-1">
          <div className="flex justify-between text-slate-500">
            <span>24h Vol</span>
            <span className="text-slate-300">{formatDexData(pos.volume_24h, true)}</span>
          </div>
          <div className="flex justify-between text-slate-500">
            <span>Mkt Cap</span>
            <span className="text-slate-300">{formatDexData(pos.market_cap, true)}</span>
          </div>
        </div>
        <div className="flex flex-col gap-1 border-l border-slate-800 pl-2">
          <div className="flex justify-between text-slate-500">
            <span>5m Chg</span>
            <span className={(pos.price_change_5m || 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'}>
              {(pos.price_change_5m || 0) >= 0 ? '+' : ''}{formatDexData(pos.price_change_5m, false)}
            </span>
          </div>
          <div className="flex justify-between text-slate-500">
            <span>B/S (5m)</span>
            {formatDexPair(pos.txns_5m_buys, pos.txns_5m_sells)}
          </div>
        </div>
      </div>

      {/* Stats Row — 净盈亏精度 6 位小数 */}
      <Flex className="mb-4 bg-slate-950/50 p-3 rounded-lg flex-wrap gap-y-2">
        <div className="text-center w-1/3 border-r border-slate-800/50">
          <Text className="text-slate-500 text-xs">投资额</Text>
          <Text className="text-slate-300 font-mono text-sm">{investedBnb.toFixed(4)}</Text>
        </div>
        <div className="text-center w-1/3 border-r border-slate-800/50">
          <Text className="text-slate-500 text-xs">价值</Text>
          <Text className="text-white font-mono text-sm">{currentValueBnb.toFixed(4)}</Text>
        </div>
        <div className="text-center w-1/3">
          <Text className="text-slate-500 text-xs">净盈亏</Text>
          <Text className={`font-mono text-sm ${isProfitable ? 'text-emerald-400' : 'text-rose-400'}`}>
            {formatPnl(netPnlBnb)}
          </Text>
        </div>

        {(pos.realized_pnl_bnb && Math.abs(pos.realized_pnl_bnb) > 0) ? (
          <div className="w-full mt-2 pt-2 border-t border-slate-800/30 flex justify-between items-center px-2">
            <Text className="text-slate-500 text-xs flex items-center gap-1">
              <DollarSign className="w-3 h-3" /> 已实现盈亏
            </Text>
            <Text className={`font-mono text-sm ${pos.realized_pnl_bnb >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
              {formatPnl(pos.realized_pnl_bnb)}
            </Text>
          </div>
        ) : null}
      </Flex>

      {/* 止损 / 目标价 — 支持点击编辑 */}
      <div className="space-y-2 mb-6">
        <Flex>
          <Text className="text-slate-500 text-xs flex items-center gap-1">
            <AlertTriangle className="w-3 h-3" /> 止损价
          </Text>
          <EditablePrice
            value={pos.stop_loss_price}
            fieldName="stop_loss_price"
            tokenAddress={pos.token_address}
            color="text-rose-400"
          />
        </Flex>

        {/* 进度条：显示当前价在 [止损价, 目标价] 区间的位置 */}
        <ProgressBar
          value={progressValue}
          color={progressColor}
          className="mt-1 h-1.5"
        />
        <div className="flex justify-between text-[10px] mt-0.5">
          <span className="text-slate-600">止损</span>
          
          <span className={`font-bold ${progressColor === 'rose' ? 'text-rose-500 animate-pulse' : progressColor === 'yellow' ? 'text-yellow-500' : 'text-emerald-500'}`}>
            {statusText} (距止损 {rawDist.toFixed(1)}%)
          </span>

          <span className="text-slate-600">目标</span>
        </div>

        <Flex className="mt-1">
          <Text className="text-slate-500 text-xs flex items-center gap-1">
            <Rocket className="w-3 h-3" /> 目标价 {targetLabel && <span className="text-[10px] text-slate-500 ml-1">({targetLabel})</span>}
          </Text>
          <EditablePrice
            value={targetPrice}
            fieldName="target_price"
            tokenAddress={pos.token_address}
            color="text-emerald-400"
          />
        </Flex>
      </div>

      {/* Actions — 三个按钮全部经过确认弹窗 */}
      <div className="grid grid-cols-3 gap-2 mt-auto">
        <Button
          size="xs"
          variant="secondary"
          color="slate"
          className="w-full text-xs py-1"
          onClick={() => onSell(pos, 25)}
        >
          卖出 25%
        </Button>
        <Button
          size="xs"
          variant="secondary"
          color="slate"
          className="w-full text-xs py-1"
          onClick={() => onSell(pos, 50)}
        >
          卖出 50%
        </Button>
        <Button
          size="xs"
          variant="primary"
          color="rose"
          className="w-full text-xs py-1 font-bold"
          onClick={() => onSell(pos, 100)}
        >
          全部卖出
        </Button>
      </div>
    </Card>
  );
};

export default PositionCard;
