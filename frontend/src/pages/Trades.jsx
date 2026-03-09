import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { Card, Title, Text, Badge, Select, SelectItem, Metric, TextInput } from '@tremor/react';
import api from '../utils/api';
import { formatNumber, formatTimeAgo, formatPrice } from '../utils/formatters';
import { ExternalLinkIcon, ChevronDownIcon, ChevronRightIcon, SearchIcon } from 'lucide-react';
import GmgnLink from '../components/common/GmgnLink';

const PAGE_SIZE = 100;

const parseDate = (value) => {
  if (value === undefined || value === null || value === '') return null;
  if (value instanceof Date) return value;
  const numeric = Number(value);
  if (!Number.isNaN(numeric)) {
    if (numeric > 1e12) return new Date(numeric);
    if (numeric > 1e10) return new Date(numeric);
    if (numeric > 1e9) return new Date(numeric * 1000);
    if (numeric > 1e5) return new Date(numeric * 1000);
  }
  const normalized = String(value).replace('T', ' ').replace('Z', '');
  const iso = normalized.includes(' ') ? normalized.replace(' ', 'T') : normalized;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return date;
};

const formatDateTime = (value) => {
  const date = parseDate(value);
  if (!date) return '--';
  const pad = (num) => String(num).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
};

const toNumber = (value) => {
  if (value === undefined || value === null || value === '') return null;
  const num = Number(value);
  if (Number.isNaN(num)) return null;
  return num;
};

const normalizeAmount = (value) => {
  const num = toNumber(value);
  if (num === null) return null;
  if (Math.abs(num) > 1e12) return num / 1e18;
  return num;
};

const DELAYED_HONEYPOT_REASONS = new Set(['liq_drain_2min', 'no_momentum_2min']);
const isDelayedHoneypot = (reason) => DELAYED_HONEYPOT_REASONS.has(reason);

const normalizeReason = (reason) => {
  if (!reason) return '';
  if (reason === 'take_profit') return '🎯止盈';
  if (reason === 'time_stop') return '⏱️时间止损';
  if (reason === 'stop_loss') return '🛑止损';
  if (reason === 'rug') return '☠️Rug';
  if (reason === 'manual') return '🖐️手动';
  if (reason === 'liq_drain_2min') return '🎭延迟貔貅·撤池';
  if (reason === 'no_momentum_2min') return '🎭延迟貔貅·无热度';
  return reason;
};

const getLineColor = (trade) => {
  if (trade.trade_type === 'simulation') return 'bg-gray-300';
  if (trade.action === 'buy') return 'bg-blue-500';
  const pnl = trade.pnl_bnb ?? trade.pnl_percentage ?? 0;
  return pnl >= 0 ? 'bg-emerald-500' : 'bg-rose-500';
};

const getTypeBadge = (trade) => {
  if (trade.status === 'failed_rug') {
    return { text: 'SELL·失败', color: 'rose', className: 'border border-rose-500 bg-transparent text-rose-600' };
  }
  if (trade.action === 'buy') {
    return { text: 'BUY', color: 'blue', className: '' };
  }
  if (trade.trade_type === 'simulation') {
    return { text: 'SELL', color: 'slate', className: 'border border-gray-400 bg-transparent text-gray-600' };
  }
  const pnl = trade.pnl_bnb ?? trade.pnl_percentage ?? 0;
  return pnl >= 0 ? { text: 'SELL', color: 'emerald', className: '' } : { text: 'SELL', color: 'rose', className: '' };
};

const formatPnl = (pnlBnb, pnlPercent) => {
  if (pnlBnb === null || pnlBnb === undefined) return { text: '--', className: 'text-gray-400' };
  const sign = pnlBnb >= 0 ? '+' : '';
  const percentText = pnlPercent !== null && pnlPercent !== undefined ? ` (${pnlPercent >= 0 ? '+' : ''}${formatNumber(pnlPercent, 2)}%)` : '';
  return {
    text: `${sign}${formatNumber(pnlBnb, 4)} BNB${percentText}`,
    className: pnlBnb >= 0 ? 'text-emerald-600' : 'text-rose-600'
  };
};

const formatGroupPnl = (group) => {
  if (group.totalPnlBnb === 0 && group.totalSellBnb === 0) return '持仓中';
  const sign = group.totalPnlBnb >= 0 ? '+' : '';
  const percentText = group.pnlPercent !== null ? ` (${group.pnlPercent >= 0 ? '+' : ''}${formatNumber(group.pnlPercent, 2)}%)` : '';
  return `${sign}${formatNumber(group.totalPnlBnb, 4)} BNB${percentText}`;
};

const formatCost = (trade) => {
  if ((trade.slippage_pct === undefined || trade.slippage_pct === null) && (trade.gas_cost_bnb === undefined || trade.gas_cost_bnb === null)) {
    return <span className="text-gray-400">--</span>;
  }
  const slippagePct = trade.slippage_pct || 0;
  const gasCost = trade.gas_cost_bnb || 0;
  return (
    <div className="flex flex-col items-end text-xs">
      <span className={slippagePct > 5 ? "text-rose-500 font-bold" : "text-gray-600"}>
        滑: {formatNumber(slippagePct, 2)}%
      </span>
      <span className="text-gray-400">Gas: {formatNumber(gasCost, 5)}</span>
    </div>
  );
};

const isRealHash = (hash) => typeof hash === 'string' && hash.startsWith('0x') && hash.length > 12;

const Trades = () => {
  const [trades, setTrades] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [totalCount, setTotalCount] = useState(0);

  const [filterType, setFilterType] = useState('all');
  const [filterPnl, setFilterPnl] = useState('all');
  const [filterTime, setFilterTime] = useState('7d');   // 默认近7天
  const [searchToken, setSearchToken] = useState('');
  const [expandedGroups, setExpandedGroups] = useState({});
  const [todayStats, setTodayStats] = useState(null);

  const searchTimerRef = useRef(null);
  // 保存当前"已发送给API"的搜索词，避免闭包问题
  const committedSearchRef = useRef('');

  // ─── 今日统计（独立轮询） ───
  const fetchTodayStats = useCallback(async () => {
    try {
      const res = await api.get('/status');
      if (res) {
        setTodayStats({
          totalBuy: res.today_buy_bnb || 0,
          totalSell: res.today_sell_bnb || 0,
          totalPnl: res.today_profit_bnb || 0,
          winRate: res.win_rate || 0
        });
      }
    } catch {}
  }, []);

  useEffect(() => {
    fetchTodayStats();
    const iv = setInterval(fetchTodayStats, 10000);
    return () => clearInterval(iv);
  }, [fetchTodayStats]);

  // ─── 核心 fetch（支持追加模式） ───
  const fetchTrades = useCallback(async ({ targetPage = 1, append = false, search = undefined, type = undefined, timeRange = undefined } = {}) => {
    const actualSearch = search !== undefined ? search : committedSearchRef.current;
    const actualType = type !== undefined ? type : filterType;
    const actualTimeRange = timeRange !== undefined ? timeRange : filterTime;

    if (append) {
      setLoadingMore(true);
    } else {
      setLoading(true);
    }

    try {
      const params = { page: targetPage, limit: PAGE_SIZE };
      if (actualType !== 'all') params.type = actualType;
      if (actualTimeRange !== 'all') params.time_range = actualTimeRange;
      if (actualSearch) params.token_search = actualSearch;

      const response = await api.get('/trades', { params });

      const items = response?.items ?? (Array.isArray(response) ? response : []);
      const total = response?.total ?? items.length;
      const more = response?.has_more ?? false;

      setTrades(prev => append ? [...prev, ...items] : items);
      setTotalCount(total);
      setHasMore(more);
      setPage(targetPage);
    } catch (error) {
      console.error('Failed to fetch trades:', error);
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterType, filterTime]);

  // ─── 过滤器变化 → 重置到第1页 ───
  useEffect(() => {
    fetchTrades({ targetPage: 1, append: false });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterType, filterTime]);

  // ─── 搜索输入（防抖300ms，服务端过滤） ───
  const handleSearchChange = (e) => {
    const val = e.target.value;
    setSearchToken(val);
    clearTimeout(searchTimerRef.current);
    searchTimerRef.current = setTimeout(() => {
      committedSearchRef.current = val;
      fetchTrades({ targetPage: 1, append: false, search: val });
    }, 300);
  };

  // ─── 加载更多 ───
  const handleLoadMore = () => {
    fetchTrades({ targetPage: page + 1, append: true });
  };

  // ─── 格式化每条交易 ───
  const normalizedTrades = useMemo(() => {
    return (trades || []).map((trade) => {
      const action = (trade.action || '').toLowerCase();
      const amountToken = normalizeAmount(trade.amount_token ?? trade.amount);
      let amountBnb = normalizeAmount(trade.amount_bnb);
      let priceBnb = normalizeAmount(trade.price_bnb ?? trade.price);
      if ((amountBnb === null || amountBnb === undefined) && amountToken !== null && priceBnb !== null) {
        amountBnb = amountToken * priceBnb;
      }
      if ((priceBnb === null || priceBnb === undefined) && amountToken !== null && amountBnb !== null && amountToken !== 0) {
        priceBnb = amountBnb / amountToken;
      }
      const createdAt = trade.created_at ?? trade.timestamp ?? trade.time;
      const parsedDate = parseDate(createdAt);
      return {
        ...trade,
        action,
        amount_token: amountToken,
        amount_bnb: amountBnb,
        price_bnb: priceBnb,
        created_at: createdAt,
        created_at_text: formatDateTime(createdAt),
        created_at_relative: formatTimeAgo(createdAt),
        _ts: parsedDate ? parsedDate.getTime() : 0,
        trade_type: trade.trade_type || 'live',
        pnl_bnb: toNumber(trade.pnl_bnb),
        pnl_percentage: toNumber(trade.pnl_percentage),
        token_symbol: trade.token_symbol || trade.token_name || 'Unknown'
      };
    });
  }, [trades]);

  // ─── 按 token 分组 + 客户端 PnL 过滤 ───
  const groupedTrades = useMemo(() => {
    const map = new Map();
    normalizedTrades.forEach((trade) => {
      const key = trade.token_address || trade.token_symbol || 'unknown';
      if (!map.has(key)) {
        map.set(key, {
          key,
          token_symbol: trade.token_symbol || trade.token_name || 'Unknown',
          token_name: trade.token_name || trade.token_symbol || 'Unknown',
          token_address: trade.token_address,
          trades: []
        });
      }
      map.get(key).trades.push(trade);
    });

    const groups = Array.from(map.values()).map((group) => {
      const sorted = [...group.trades].sort((a, b) => b._ts - a._ts);
      const buys = sorted.filter(t => t.action === 'buy');
      const sells = sorted.filter(t => t.action === 'sell');
      const totalBuyBnb = buys.reduce((acc, t) => acc + (t.amount_bnb || 0), 0);
      const totalSellBnb = sells.reduce((acc, t) => acc + (t.amount_bnb || 0), 0);
      const totalPnlBnb = sells.reduce((acc, t) => acc + (t.pnl_bnb || 0), 0);
      const pnlPercent = totalBuyBnb > 0 ? (totalPnlBnb / totalBuyBnb) * 100 : null;
      return {
        ...group,
        trades: sorted,
        totalBuyBnb,
        totalSellBnb,
        totalPnlBnb,
        pnlPercent,
        latestTs: sorted[0]?._ts ?? 0,
        hasDelayedHoneypot: sells.some(t => isDelayedHoneypot(t.close_reason)),
        isFourMeme: sorted.some(t => t.dex_name === 'four_meme')
      };
    });

    // 客户端 PnL 过滤（需要 group 级别的数据）
    return groups
      .filter(g => {
        if (filterPnl === 'profit' && g.totalPnlBnb <= 0 && g.totalSellBnb > 0) return false;
        if (filterPnl === 'loss' && g.totalPnlBnb >= 0 && g.totalSellBnb > 0) return false;
        return true;
      })
      .sort((a, b) => b.latestTs - a.latestTs);
  }, [normalizedTrades, filterPnl]);

  const getBscScanLink = (hash) => `https://bscscan.com/tx/${hash}`;

  const hasActiveFilter = searchToken || filterType !== 'all' || filterTime !== '7d' || filterPnl !== 'all';

  return (
    <div className="p-6 space-y-6">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <Title>交易记录</Title>
          <Text>
            共 {totalCount} 条记录 · 当前显示 {groupedTrades.length} 个币种
            {filterTime !== 'all' && <span className="ml-1 text-indigo-500">({filterTime === 'today' ? '今天' : filterTime === '7d' ? '近7天' : '近30天'})</span>}
          </Text>
        </div>
      </div>

      {/* Filter Bar */}
      <div className="flex flex-wrap items-center gap-3">
        <TextInput
          icon={SearchIcon}
          placeholder="搜索币种名称/地址..."
          value={searchToken}
          onChange={handleSearchChange}
          className="w-56"
        />
        <div className="w-36">
          <Select value={filterType} onValueChange={(v) => { setFilterType(v); }}>
            <SelectItem value="all">全部类型</SelectItem>
            <SelectItem value="buy">买入</SelectItem>
            <SelectItem value="sell">卖出</SelectItem>
          </Select>
        </div>
        <div className="w-36">
          <Select value={filterTime} onValueChange={(v) => { setFilterTime(v); }}>
            <SelectItem value="all">全部时间</SelectItem>
            <SelectItem value="today">今天</SelectItem>
            <SelectItem value="7d">近7天</SelectItem>
            <SelectItem value="30d">近30天</SelectItem>
          </Select>
        </div>
        <div className="w-36">
          <Select value={filterPnl} onValueChange={(v) => { setFilterPnl(v); }}>
            <SelectItem value="all">全部盈亏</SelectItem>
            <SelectItem value="profit">仅盈利</SelectItem>
            <SelectItem value="loss">仅亏损</SelectItem>
          </Select>
        </div>
        {hasActiveFilter && (
          <button
            onClick={() => {
              setSearchToken('');
              setFilterType('all');
              setFilterTime('7d');
              setFilterPnl('all');
              committedSearchRef.current = '';
            }}
            className="text-xs text-indigo-600 hover:text-indigo-800 underline"
          >清除筛选</button>
        )}
      </div>

      {/* 统计卡片 */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <Card>
          <Text>今日总买入</Text>
          <Metric>{formatNumber(todayStats?.totalBuy || 0, 4)} BNB</Metric>
        </Card>
        <Card>
          <Text>今日总卖出</Text>
          <Metric>{formatNumber(todayStats?.totalSell || 0, 4)} BNB</Metric>
        </Card>
        <Card>
          <Text>今日净盈亏</Text>
          <Metric className={(todayStats?.totalPnl || 0) >= 0 ? 'text-emerald-600' : 'text-rose-600'}>
            {(todayStats?.totalPnl || 0) >= 0 ? '+' : ''}{formatNumber(todayStats?.totalPnl || 0, 4)} BNB
          </Metric>
        </Card>
        <Card>
          <Text>胜率</Text>
          <Metric>{formatNumber(todayStats?.winRate || 0, 2)}%</Metric>
        </Card>
      </div>

      <Card>
        {loading && trades.length === 0 ? (
          <div className="text-center py-8 text-gray-500">加载中...</div>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm text-left">
                <thead className="text-gray-500 font-medium border-b border-gray-200">
                  <tr>
                    <th className="py-3 px-2"></th>
                    <th className="py-3 px-4">时间</th>
                    <th className="py-3 px-4">代币</th>
                    <th className="py-3 px-4">类型</th>
                    <th className="py-3 px-4 text-right">数量</th>
                    <th className="py-3 px-4 text-right">价格 (BNB)</th>
                    <th className="py-3 px-4 text-right">总额 (BNB)</th>
                    <th className="py-3 px-4 text-right">交易成本</th>
                    <th className="py-3 px-4 text-right">盈亏</th>
                    <th className="py-3 px-4">交易哈希</th>
                    <th className="py-3 px-4">状态</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {groupedTrades.length > 0 ? (
                    groupedTrades.map((group) => {
                      const expanded = expandedGroups[group.key] ?? false;
                      return (
                        <React.Fragment key={group.key}>
                          <tr
                            className="bg-gray-50 hover:bg-gray-100 cursor-pointer"
                            onClick={() => setExpandedGroups((prev) => ({ ...prev, [group.key]: !expanded }))}
                          >
                            <td colSpan="11" className="py-3 px-4">
                              <div className="flex items-center justify-between">
                                <div className="flex items-center gap-3">
                                  {expanded ? <ChevronDownIcon className="w-4 h-4 text-gray-500" /> : <ChevronRightIcon className="w-4 h-4 text-gray-500" />}
                                  <span className="font-semibold text-gray-900">{group.token_symbol}</span>
                                  {group.isFourMeme && (
                                    <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-bold bg-purple-100 text-purple-700 border border-purple-200">4M</span>
                                  )}
                                  {group.hasDelayedHoneypot && (
                                    <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-purple-100 text-purple-700 border border-purple-200">🎭 延迟貔貅</span>
                                  )}
                                  {group.token_name && group.token_name !== group.token_symbol && (
                                    <span className="text-sm font-normal text-gray-500">({group.token_name})</span>
                                  )}
                                  <span className="text-xs text-gray-500 truncate max-w-[200px]">{group.token_address}</span>
                                  <GmgnLink address={group.token_address} />
                                </div>
                                <div className="flex flex-wrap items-center gap-4 text-xs text-gray-600">
                                  <span>买入: {formatNumber(group.totalBuyBnb, 4)} BNB</span>
                                  <span className={group.totalPnlBnb >= 0 ? 'text-emerald-600' : 'text-rose-600'}>
                                    最终盈亏: {formatGroupPnl(group)}
                                  </span>
                                  <span className="text-indigo-600">{expanded ? '收起' : '展开'}</span>
                                </div>
                              </div>
                            </td>
                          </tr>
                          {expanded && group.trades.map((trade) => {
                            const badge = getTypeBadge(trade);
                            const pnlDisplay = trade.action === 'sell'
                              ? (trade.status === 'failed_rug'
                                  ? { text: '💀已归零', className: 'text-gray-500 italic' }
                                  : formatPnl(trade.pnl_bnb, trade.pnl_percentage))
                              : {
                                text: group.totalSellBnb > 0 ? `已实现 ${formatGroupPnl(group)}` : '持仓中',
                                className: group.totalSellBnb > 0 ? (group.totalPnlBnb >= 0 ? 'text-emerald-600' : 'text-rose-600') : 'text-gray-500'
                              };
                            return (
                              <tr key={trade.id || trade.tx_hash || `${group.key}-${trade._ts}`} className="hover:bg-gray-50/50 transition-colors">
                                <td className="relative w-2 px-0">
                                  <span className={`absolute left-0 top-0 bottom-0 w-1 ${getLineColor(trade)}`}></span>
                                </td>
                                <td className="py-3 px-4 whitespace-nowrap text-gray-600" title={trade.created_at_relative}>
                                  {trade.created_at_text}
                                </td>
                                <td className="py-3 px-4 font-medium text-gray-900">
                                  <div className="flex flex-col">
                                    <span>
                                      {(trade.token_symbol && trade.token_symbol !== '$') ? trade.token_symbol : (trade.token_name && trade.token_name !== '$' ? trade.token_name : (trade.token_address ? trade.token_address.substring(0,8) : 'Unknown'))}
                                      {trade.token_name && trade.token_name !== '$' && trade.token_name !== trade.token_symbol && trade.token_symbol !== '$' && (
                                        <span className="ml-1 text-xs text-gray-500">({trade.token_name})</span>
                                      )}
                                    </span>
                                    <span className="text-xs text-gray-400 font-mono truncate max-w-[100px]" title={trade.token_address}>
                                      {trade.token_address}
                                    </span>
                                    <GmgnLink address={trade.token_address} />
                                  </div>
                                </td>
                                <td className="py-3 px-4">
                                  <div className="flex items-center gap-1">
                                    <Badge color={badge.color} size="xs" className={badge.className}>
                                      {badge.text}
                                      {trade.trade_type === 'simulation' && <span className="ml-1 text-[10px] opacity-70">模拟</span>}
                                    </Badge>
                                    {trade.dex_name === 'four_meme' && (
                                      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-bold bg-purple-100 text-purple-700 border border-purple-200">4M</span>
                                    )}
                                  </div>
                                </td>
                                <td className="py-3 px-4 text-right font-mono">{formatNumber(trade.amount_token)}</td>
                                <td className="py-3 px-4 text-right font-mono text-gray-600">{formatPrice(trade.price_bnb)}</td>
                                <td className="py-3 px-4 text-right font-bold text-gray-900 font-mono">
                                  {trade.status === 'failed_rug' ? <span className="bg-gray-100 text-gray-500 px-1 rounded text-xs">0.000</span> : formatNumber(trade.amount_bnb, 4)}
                                </td>
                                <td className="py-3 px-4 text-right font-mono text-gray-600">
                                  {formatCost(trade)}
                                </td>
                                <td className={`py-3 px-4 text-right font-mono ${pnlDisplay.className}`}>
                                  {pnlDisplay.text}
                                  {trade.action === 'sell' && trade.close_reason ? (
                                    <span className="ml-2 text-xs text-gray-500">{normalizeReason(trade.close_reason)}</span>
                                  ) : null}
                                </td>
                                <td className="py-3 px-4">
                                  {isRealHash(trade.tx_hash) ? (
                                    <a href={getBscScanLink(trade.tx_hash)} target="_blank" rel="noopener noreferrer"
                                      className="text-indigo-600 hover:text-indigo-800 flex items-center gap-1 text-xs">
                                      {trade.tx_hash.substring(0, 6)}...{trade.tx_hash.substring(trade.tx_hash.length - 4)}
                                      <ExternalLinkIcon className="w-3 h-3" />
                                    </a>
                                  ) : (trade.tx_hash && (trade.tx_hash.startsWith('SIM_') || trade.tx_hash.startsWith('simulated_'))) ? (
                                    <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-500 border border-gray-200 cursor-default">模拟</span>
                                  ) : (
                                    <span className="text-xs text-gray-400">--</span>
                                  )}
                                </td>
                                <td className="py-3 px-4">
                                  <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
                                    trade.status === 'success' ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'
                                  }`}>
                                    {trade.status || '--'}
                                  </span>
                                </td>
                              </tr>
                            );
                          })}
                        </React.Fragment>
                      );
                    })
                  ) : (
                    <tr>
                      <td colSpan="11" className="text-center py-8 text-gray-500">暂无交易记录</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            {/* 分页栏 */}
            <div className="flex justify-between items-center mt-4 pt-4 border-t border-gray-100">
              <span className="text-sm text-gray-400">
                显示 {trades.length} / {totalCount} 条 · {groupedTrades.length} 个币种
              </span>
              {hasMore && (
                <button
                  onClick={handleLoadMore}
                  disabled={loadingMore}
                  className="px-4 py-2 text-sm bg-indigo-50 hover:bg-indigo-100 text-indigo-700 rounded-lg transition-colors disabled:opacity-50"
                >
                  {loadingMore ? '加载中...' : `加载更多 (剩余 ${totalCount - trades.length} 条)`}
                </button>
              )}
            </div>
          </>
        )}
      </Card>
    </div>
  );
};

export default Trades;
