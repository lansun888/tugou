import React, { useState, useEffect } from 'react';
import { Card, Title, Text, Select, SelectItem, TextInput } from '@tremor/react';
import { SearchIcon, EyeIcon, ArrowRightIcon, ArrowLeftIcon, CopyIcon, CheckIcon, ExternalLinkIcon } from 'lucide-react';
import api from '../utils/api';
import DiscoveryDrawer from '../components/common/DiscoveryDrawer';
import { formatDate } from '../utils/formatters';

// 从嵌套 check_details(raw_data) 中提取平铺字段
const extractDetails = (raw = {}) => {
  const goplus = raw.goplus || {};
  const honeypot = raw.honeypot || {};
  const simulation = raw.simulation || {};
  const holders = raw.holders || {};
  const contract = raw.contract || {};

  // is_honeypot
  const isHoneypot =
    simulation.is_honeypot === true ||
    goplus.is_honeypot === '1' ||
    honeypot.isHoneypot === true ||
    false;

  // buy_tax: goplus 存的是 0~1 小数，需 *100；simulation 存的已是百分比
  let buyTax = null;
  if (goplus.buy_tax != null) buyTax = parseFloat(goplus.buy_tax) * 100;
  else if (simulation.buy_tax != null) buyTax = parseFloat(simulation.buy_tax);
  else if (honeypot.simulationResult?.buyTax != null) buyTax = parseFloat(honeypot.simulationResult.buyTax);

  let sellTax = null;
  if (goplus.sell_tax != null) sellTax = parseFloat(goplus.sell_tax) * 100;
  else if (simulation.sell_tax != null) sellTax = parseFloat(simulation.sell_tax);
  else if (honeypot.simulationResult?.sellTax != null) sellTax = parseFloat(honeypot.simulationResult.sellTax);

  // holder_concentration: top_5_share 来自 holders
  const holderConcentration = holders.top_5_share != null ? parseFloat(holders.top_5_share) : null;

  // is_open_source
  const isOpenSource = !!contract.SourceCode || goplus.is_open_source === '1';

  // is_renounced
  const isRenounced = goplus.owner_address === '0x0000000000000000000000000000000000000000';

  // lp_locked
  const lpLocked = holders.lp_locked === true || goplus.lp_locked === '1';

  return { isHoneypot, buyTax, sellTax, holderConcentration, isOpenSource, isRenounced, lpLocked };
};

// 纯文字颜色，无背景色块
const getScoreStyle = (score) => {
  if (score >= 80) return { text: 'text-emerald-600', dot: 'bg-emerald-500' };
  if (score >= 60) return { text: 'text-yellow-600', dot: 'bg-yellow-500' };
  if (score >= 40) return { text: 'text-orange-600', dot: 'bg-orange-500' };
  return { text: 'text-rose-600', dot: 'bg-rose-500' };
};

// 状态标签：无背景色块，只有文字颜色 + 细边框
const STATUS_MAP = {
  bought:    { label: '已买入', cls: 'text-emerald-700 border border-emerald-300' },
  rejected:  { label: '已拒绝', cls: 'text-rose-600 border border-rose-300' },
  analyzing: { label: '分析中', cls: 'text-gray-500 border border-gray-300' },
};

const StatusBadge = ({ status, reason }) => {
  const s = STATUS_MAP[status] || { label: status, cls: 'text-gray-500 border border-gray-200' };
  const reasons = reason ? reason.split(',').map(r => r.trim()).filter(Boolean) : [];
  return (
    <div className="relative group inline-block">
      <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${s.cls}`}>
        {s.label}
      </span>
      {/* 拒绝原因 tooltip，显示在右侧 */}
      {status === 'rejected' && reasons.length > 0 && (
        <div className="absolute left-full top-1/2 -translate-y-1/2 ml-2 hidden group-hover:block z-50 w-52 pointer-events-none">
          <div className="bg-white border border-gray-200 rounded-lg shadow-xl p-3 text-xs">
            <div className="font-semibold text-gray-700 mb-1.5 pb-1 border-b border-gray-100">拒绝原因</div>
            <div className="space-y-1">
              {reasons.map((r, i) => (
                <div key={i} className="flex items-start gap-1 text-gray-600">
                  <span className="text-rose-400 mt-0.5 flex-shrink-0">•</span>
                  <span>{r}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

const ScoreChip = ({ score, details = {} }) => {
  const style = getScoreStyle(score);
  const flat = extractDetails(details);

  const dims = [
    { label: '貔貅检测', value: flat.isHoneypot, type: 'bool', good: false },
    { label: '合约开源', value: flat.isOpenSource, type: 'bool', good: true },
    { label: '权限放弃', value: flat.isRenounced, type: 'bool', good: true },
    { label: '流动性锁定', value: flat.lpLocked, type: 'bool', good: true },
    { label: '买税', value: flat.buyTax, type: 'pct' },
    { label: '卖税', value: flat.sellTax, type: 'pct' },
    { label: '前5持仓', value: flat.holderConcentration, type: 'pct' },
  ];

  return (
    <div className="relative group inline-block">
      {/* 无背景色块，仅文字+圆点 */}
      <span className={`inline-flex items-center gap-1.5 text-xs font-semibold cursor-help ${style.text}`}>
        <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${style.dot}`} />
        {score}/100
      </span>
      {/* 评分明细 tooltip，显示在右侧，避免遮挡左侧数据 */}
      <div className="absolute left-full top-1/2 -translate-y-1/2 ml-2 hidden group-hover:block z-50 w-56 pointer-events-none">
        <div className="bg-white border border-gray-200 rounded-lg shadow-xl p-3 text-xs">
          <div className="font-semibold text-gray-700 mb-2 pb-1 border-b border-gray-100">安全评分明细</div>
          <div className="space-y-1.5">
            {dims.map(({ label, value, type, good }) => {
              let display, dotColor;
              if (type === 'pct') {
                display = value != null ? `${value.toFixed(1)}%` : '未知';
                dotColor = value != null ? 'bg-blue-400' : 'bg-gray-300';
              } else {
                if (value === undefined || value === null) {
                  display = '未检测'; dotColor = 'bg-gray-300';
                } else {
                  const isGood = good ? value : !value;
                  display = value ? '是' : '否';
                  dotColor = isGood ? 'bg-emerald-500' : 'bg-rose-500';
                }
              }
              return (
                <div key={label} className="flex items-center justify-between">
                  <span className="text-gray-500">{label}</span>
                  <div className="flex items-center gap-1.5">
                    <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${dotColor}`} />
                    <span className="font-medium text-gray-700">{display}</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
};

const CopyButton = ({ text }) => {
  const [copied, setCopied] = useState(false);

  const handleCopy = (e) => {
    e.stopPropagation();
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <button 
      onClick={handleCopy}
      className="ml-1 p-0.5 text-gray-400 hover:text-gray-600 rounded transition-colors"
      title="复制地址"
    >
      {copied ? <CheckIcon className="w-3 h-3 text-green-500" /> : <CopyIcon className="w-3 h-3" />}
    </button>
  );
};

const GmgnLink = ({ address }) => (
  <a
    href={`https://gmgn.ai/bsc/token/${address}`}
    target="_blank"
    rel="noopener noreferrer"
    className="ml-1 p-0.5 text-gray-400 hover:text-indigo-500 rounded transition-colors"
    title="在 GMGN.ai 查看"
    onClick={(e) => e.stopPropagation()}
  >
    <ExternalLinkIcon className="w-3 h-3" />
  </a>
);

const Discoveries = () => {
  const [discoveries, setDiscoveries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(true);
  const [statusFilter, setStatusFilter] = useState('all');
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedDiscovery, setSelectedDiscovery] = useState(null);
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);

  const fetchDiscoveries = async (isPolling = false) => {
    if (!isPolling) setLoading(true);
    try {
      const params = new URLSearchParams();
      params.append('page', page);
      params.append('limit', 20);
      if (statusFilter !== 'all') params.append('result', statusFilter);
      if (searchTerm) params.append('search', searchTerm);

      const res = await api.get(`/discoveries?${params.toString()}`);
      if (res && Array.isArray(res)) {
        setDiscoveries(res);
        setHasMore(res.length === 20);
      } else {
        if (!isPolling) setDiscoveries([]);
        setHasMore(false);
      }
    } catch (error) {
      console.error('Failed to fetch discoveries:', error);
    } finally {
      if (!isPolling) setLoading(false);
    }
  };

  useEffect(() => {
    fetchDiscoveries();
    const interval = setInterval(() => fetchDiscoveries(true), 10000);
    return () => clearInterval(interval);
  }, [page, statusFilter]);

  useEffect(() => {
    const timer = setTimeout(() => {
      if (page === 1) fetchDiscoveries();
      else setPage(1);
    }, 500);
    return () => clearTimeout(timer);
  }, [searchTerm]);

  const handleViewDetails = (discovery) => {
    setSelectedDiscovery(discovery);
    setIsDrawerOpen(true);
  };

  return (
    <div className="p-6 space-y-6">
      <DiscoveryDrawer
        isOpen={isDrawerOpen}
        closeModal={() => setIsDrawerOpen(false)}
        discovery={selectedDiscovery}
      />

      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <Title>新币发现</Title>
          <Text>监控到的新代币及其安全分析结果</Text>
        </div>
        <div className="flex flex-col sm:flex-row gap-2 w-full sm:w-auto">
          <TextInput
            icon={SearchIcon}
            placeholder="搜索代币名称/地址..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="sm:w-64"
          />
          <Select
            value={statusFilter}
            onValueChange={(val) => { setStatusFilter(val); setPage(1); }}
            className="sm:w-40"
          >
            <SelectItem value="all">全部状态</SelectItem>
            <SelectItem value="analyzing">分析中</SelectItem>
            <SelectItem value="bought">已买入</SelectItem>
            <SelectItem value="rejected">已拒绝</SelectItem>
          </Select>
        </div>
      </div>

      <Card>
        {loading && discoveries.length === 0 ? (
          <div className="text-center py-12 text-gray-500">加载中...</div>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm text-left">
                <thead className="text-gray-500 font-medium border-b border-gray-200">
                  <tr>
                    <th className="py-3 px-4">时间</th>
                    <th className="py-3 px-4">代币</th>
                    <th className="py-3 px-4">初始流动性</th>
                    <th className="py-3 px-4">安全评分</th>
                    <th className="py-3 px-4">状态 / 拒绝原因</th>
                    <th className="py-3 px-4 text-right">操作</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {discoveries.length > 0 ? (
                    discoveries.map((item) => (
                      <tr key={item.pair_address} className="hover:bg-gray-50/50 transition-colors">
                        <td className="py-3 px-4 whitespace-nowrap text-gray-600">
                          {formatDate(item.discovery_time)}
                        </td>
                        <td className="py-3 px-4">
                          <div className="flex flex-col">
                            <span className="font-medium text-gray-900">{item.token_symbol}</span>
                            <div className="flex items-center">
                              <span className="text-xs text-gray-400 font-mono truncate max-w-[100px]" title={item.token_address}>
                                {item.token_address}
                              </span>
                              <CopyButton text={item.token_address} />
                              <GmgnLink address={item.token_address} />
                            </div>
                          </div>
                        </td>
                        <td className="py-3 px-4 font-mono text-gray-700">
                          {parseFloat(item.initial_liquidity || 0).toFixed(4)} BNB
                        </td>
                        <td className="py-3 px-4">
                          {item.security_score != null ? (
                            <ScoreChip score={item.security_score} details={item.check_details || {}} />
                          ) : (
                            <span className="text-gray-400 text-xs">—</span>
                          )}
                        </td>
                        <td className="py-3 px-4">
                          <div className="flex items-center gap-1.5 flex-wrap">
                            <StatusBadge status={item.status} reason={item.risk_reason} />
                            {item.is_delayed_honeypot && (
                              <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-purple-50 text-purple-700 border border-purple-200" title="买入后2分钟评估被判定为延迟貔貅">🎭 延迟貔貅</span>
                            )}
                          </div>
                        </td>
                        <td className="py-3 px-4 text-right">
                          <button
                            className="inline-flex items-center gap-1 text-xs text-indigo-600 hover:text-indigo-800 font-medium"
                            onClick={() => handleViewDetails(item)}
                          >
                            <EyeIcon className="w-3.5 h-3.5" />
                            详情
                          </button>
                        </td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan="6" className="text-center py-12 text-gray-500">
                        暂无发现记录
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            <div className="flex justify-between items-center mt-4 pt-4 border-t border-gray-100">
              <button
                onClick={() => setPage(p => Math.max(1, p - 1))}
                disabled={page === 1}
                className="flex items-center gap-1 text-sm text-gray-600 hover:text-gray-900 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <ArrowLeftIcon className="w-4 h-4" /> 上一页
              </button>
              <span className="text-sm text-gray-500">第 {page} 页</span>
              <button
                onClick={() => setPage(p => p + 1)}
                disabled={!hasMore}
                className="flex items-center gap-1 text-sm text-gray-600 hover:text-gray-900 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                下一页 <ArrowRightIcon className="w-4 h-4" />
              </button>
            </div>
          </>
        )}
      </Card>
    </div>
  );
};

export default Discoveries;
