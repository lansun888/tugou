import React, { useState, useRef } from 'react';
import { Card, Text, Flex, Badge, Button, ProgressBar } from "@tremor/react";
import { Copy, Check, Clock, AlertTriangle, Rocket, DollarSign, Pencil, X } from 'lucide-react';
import { formatPrice } from '../utils/formatters';
import api from '../utils/api';

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
  for (const ch of str) {
    if (/[A-Za-z0-9]/.test(ch)) return ch.toUpperCase();
  }
  return '?';
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
  const [isCopied, setIsCopied] = useState(false);

  const handleCopy = (e) => {
    e.stopPropagation();
    if (pos.token_address) {
      navigator.clipboard.writeText(pos.token_address);
      setIsCopied(true);
      setTimeout(() => setIsCopied(false), 2000);
    }
  };

  const isProfitable = (pos.pnl_percentage || 0) >= 0;
  const isSuperProfitable = (pos.pnl_percentage || 0) >= 100;
  const isLoss = (pos.pnl_percentage || 0) < 0;

  // 距止损百分比：(现价-止损价)/现价*100
  // 正值 = 现价高于止损（安全），负值 = 现价低于止损（追踪止损已上调）
  const rawDist = pos.stop_loss_price && pos.current_price_bnb
    ? ((pos.current_price_bnb - pos.stop_loss_price) / pos.current_price_bnb) * 100
    : 100;
  // 追踪止损上调：现价低于止损价（止损保护盈利，不等于亏损）
  const stopAboveCurrent = rawDist < 0;
  // 接近止损：距离 < 10%
  const isNearStopLoss = rawDist >= 0 && rawDist < 10;
  // 进度条值：止损上调时显示 100% 满格 danger，正常时按距离显示
  const progressValue = stopAboveCurrent ? 100 : Math.min(rawDist, 100);

  // 卡片边框：仅真实亏损(pnl<0)时红色，止损上调时琥珀，接近止损时动画琥珀
  let borderColorClass = "ring-slate-800";
  if (isLoss) borderColorClass = "ring-rose-900/50";
  else if (stopAboveCurrent || isNearStopLoss) borderColorClass = "ring-amber-500/40 animate-pulse";

  const soldPercent = pos.sold_percentage || 0;
  let soldBadge = null;
  if (soldPercent >= 99) soldBadge = <Badge color="rose" size="xs">已清仓</Badge>;
  else if (soldPercent >= 50) soldBadge = <Badge color="yellow" size="xs">已卖出 {soldPercent.toFixed(0)}%</Badge>;
  else if (soldPercent >= 25) soldBadge = <Badge color="emerald" size="xs">已卖出 {soldPercent.toFixed(0)}%</Badge>;

  // 代币头像：取第一个 ASCII 字符，跳过中文避免样式混乱
  const tokenInitial = getTokenInitial(pos.token_symbol, pos.token_name);

  return (
    <Card className={`bg-slate-900 ring-1 ${borderColorClass} transition-all hover:ring-slate-700`}>
      {/* Header */}
      <Flex className="mb-4 border-b border-slate-800 pb-3">
        <div className="flex items-center gap-3">
          <div className="bg-slate-700 w-9 h-9 rounded-full flex items-center justify-center text-sm font-bold text-slate-200 flex-shrink-0">
            {tokenInitial}
          </div>
          <div>
            <Text className="text-white font-bold text-lg flex items-center gap-2">
              {pos.token_symbol || pos.token_name}
              {isSuperProfitable && <span className="text-xs">🚀</span>}
              {soldBadge}
            </Text>
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
          <Text className="text-slate-300 font-mono text-sm">{formatPrice(pos.buy_price_bnb)}</Text>
        </div>
        <div className="border-r border-slate-800 px-2">
          <Text className="text-slate-500 text-xs mb-1">现价</Text>
          <Text className="text-white font-mono text-sm font-medium">{formatPrice(pos.current_price_bnb)}</Text>
        </div>
        <div className="pl-2">
          <Text className="text-slate-500 text-xs mb-1">盈亏</Text>
          <Text className={`font-mono text-sm font-bold ${isProfitable ? 'text-emerald-400' : 'text-rose-400'}`}>
            {isProfitable ? '+' : ''}{(pos.pnl_percentage || 0).toFixed(1)}%
          </Text>
        </div>
      </div>

      {/* DexScreener Stats */}
      <div className="grid grid-cols-2 gap-2 mb-4 text-xs border-b border-slate-800 pb-4">
        <div className="flex flex-col gap-1">
          <div className="flex justify-between text-slate-500">
            <span>24h Vol</span>
            <span className="text-slate-300">${(pos.volume_24h || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
          </div>
          <div className="flex justify-between text-slate-500">
            <span>Mkt Cap</span>
            <span className="text-slate-300">${(pos.market_cap || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
          </div>
        </div>
        <div className="flex flex-col gap-1 border-l border-slate-800 pl-2">
          <div className="flex justify-between text-slate-500">
            <span>5m Chg</span>
            <span className={(pos.price_change_5m || 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'}>
              {(pos.price_change_5m || 0) >= 0 ? '+' : ''}{(pos.price_change_5m || 0).toFixed(2)}%
            </span>
          </div>
          <div className="flex justify-between text-slate-500">
            <span>B/S (5m)</span>
            <div className="flex gap-1">
              <span className="text-emerald-400">{pos.txns_5m_buys || 0}</span>
              <span>/</span>
              <span className="text-rose-400">{pos.txns_5m_sells || 0}</span>
            </div>
          </div>
        </div>
      </div>

      {/* Stats Row — 净盈亏精度 6 位小数 */}
      <Flex className="mb-4 bg-slate-950/50 p-3 rounded-lg flex-wrap gap-y-2">
        <div className="text-center w-1/3 border-r border-slate-800/50">
          <Text className="text-slate-500 text-xs">投资额</Text>
          <Text className="text-slate-300 font-mono text-sm">{(pos.invested_bnb || 0).toFixed(4)}</Text>
        </div>
        <div className="text-center w-1/3 border-r border-slate-800/50">
          <Text className="text-slate-500 text-xs">价值</Text>
          <Text className="text-white font-mono text-sm">{(pos.current_value_bnb || 0).toFixed(4)}</Text>
        </div>
        <div className="text-center w-1/3">
          <Text className="text-slate-500 text-xs">净盈亏</Text>
          <Text className={`font-mono text-sm ${isProfitable ? 'text-emerald-400' : 'text-rose-400'}`}>
            {isProfitable ? '+' : '-'}{formatPrice(Math.abs(pos.pnl_bnb || 0))}
          </Text>
        </div>

        {(pos.realized_pnl_bnb && Math.abs(pos.realized_pnl_bnb) > 0) ? (
          <div className="w-full mt-2 pt-2 border-t border-slate-800/30 flex justify-between items-center px-2">
            <Text className="text-slate-500 text-xs flex items-center gap-1">
              <DollarSign className="w-3 h-3" /> 已实现盈亏
            </Text>
            <Text className={`font-mono text-sm ${pos.realized_pnl_bnb >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
              {pos.realized_pnl_bnb >= 0 ? '+' : '-'}{formatPrice(Math.abs(pos.realized_pnl_bnb))}
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

        {/* 进度条：正常时绿→橙渐变表示接近程度；止损上调时固定满格红色警示 */}
        <ProgressBar
          value={progressValue}
          color={stopAboveCurrent ? 'rose' : rawDist < 20 ? 'orange' : 'emerald'}
          className="mt-1 h-1.5"
        />
        <div className="flex justify-between text-[10px] mt-0.5">
          <span className="text-slate-600">止损线</span>
          {stopAboveCurrent ? (
            <span className="text-amber-400">追踪止损触发中 (低于止损 {Math.abs(rawDist).toFixed(1)}%)</span>
          ) : (
            <span className={rawDist < 10 ? 'text-rose-500' : 'text-slate-500'}>
              距止损 {rawDist.toFixed(1)}%
            </span>
          )}
          <span className="text-slate-600">现价</span>
        </div>

        <Flex className="mt-1">
          <Text className="text-slate-500 text-xs flex items-center gap-1">
            <Rocket className="w-3 h-3" /> 目标价
          </Text>
          <EditablePrice
            value={pos.target_price}
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
