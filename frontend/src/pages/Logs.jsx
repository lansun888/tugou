import React, { useState, useEffect, useRef, useMemo } from 'react';
import { Title, Text, Button, Select, SelectItem } from '@tremor/react';
import { PauseIcon, PlayIcon, TrashIcon, TerminalIcon } from 'lucide-react';
import api from '../utils/api';

const MODULE_MAPPING = {
  'monitor.pair_listener': '监控',
  'analyzer.security_checker': '分析',
  'executor.trader': '交易',
  'executor.position_manager': '持仓',
  'web.api': 'API',
  'main': '系统',
};

// 关键词高亮规则（按顺序匹配）
const HIGHLIGHT_RULES = [
  { pattern: /买入成功|买入|BUY/g,         className: 'text-emerald-400 font-bold bg-emerald-900/30 px-0.5 rounded' },
  { pattern: /卖出成功|卖出|SELL/g,         className: 'text-rose-400 font-bold bg-rose-900/30 px-0.5 rounded' },
  { pattern: /止损|止盈|TP|SL|take.profit|stop.loss/gi, className: 'text-amber-400 font-bold' },
  { pattern: /发现新代币|新代币|New Pair/g,  className: 'text-blue-400 font-bold' },
  { pattern: /Rug|貔貅|归零|蜜罐|Honeypot/gi, className: 'text-red-400 font-bold animate-pulse' },
  { pattern: /ERROR|CRITICAL/g,            className: 'text-red-500 font-bold' },
  { pattern: /WARNING/g,                   className: 'text-yellow-400' },
  { pattern: /\+[\d.]+\s*BNB/g,           className: 'text-emerald-300 font-semibold' },
  { pattern: /-[\d.]+\s*BNB/g,            className: 'text-rose-300 font-semibold' },
];

const highlightMessage = (text) => {
  if (!text) return null;
  // Build segments with highlights
  const segments = [{ text, highlighted: false, className: '' }];

  HIGHLIGHT_RULES.forEach(({ pattern, className }) => {
    const next = [];
    segments.forEach((seg) => {
      if (seg.highlighted) { next.push(seg); return; }
      const parts = seg.text.split(pattern);
      const matches = seg.text.match(pattern) || [];
      parts.forEach((part, i) => {
        if (part) next.push({ text: part, highlighted: false, className: '' });
        if (matches[i]) next.push({ text: matches[i], highlighted: true, className });
      });
    });
    segments.length = 0;
    segments.push(...next);
  });

  return segments.map((seg, i) =>
    seg.highlighted
      ? <span key={i} className={seg.className}>{seg.text}</span>
      : <span key={i}>{seg.text}</span>
  );
};

const parseLogTime = (ts) => {
  // ts format: "HH:MM:SS" or "YYYY-MM-DD HH:MM:SS"
  if (!ts) return 0;
  try {
    const d = new Date(ts.length <= 8 ? `1970-01-01T${ts}Z` : ts.replace(' ', 'T'));
    return isNaN(d) ? 0 : d.getTime();
  } catch { return 0; }
};

const Logs = () => {
  const [logs, setLogs] = useState([]);
  const [isPaused, setIsPaused] = useState(false);
  const [levelFilter, setLevelFilter] = useState('ALL');
  const [moduleFilter, setModuleFilter] = useState('ALL');
  const logsEndRef = useRef(null);
  const [isConnected, setIsConnected] = useState(true);

  const fetchLogs = async () => {
    if (isPaused) return;
    try {
      const response = await api.get('/logs?limit=200');
      setIsConnected(true);
      if (response?.logs) setLogs(response.logs);
    } catch {
      setIsConnected(false);
    }
  };

  useEffect(() => {
    fetchLogs();
    const interval = setInterval(fetchLogs, 5000);
    return () => clearInterval(interval);
  }, [isPaused]);

  useEffect(() => {
    if (!isPaused && logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logs, isPaused]);

  const getModuleDisplay = (moduleName) => {
    if (MODULE_MAPPING[moduleName]) return MODULE_MAPPING[moduleName];
    if (moduleName?.startsWith('monitor')) return '监控';
    if (moduleName?.startsWith('analyzer')) return '分析';
    if (moduleName?.startsWith('executor')) return '交易';
    return '系统';
  };

  const filteredLogs = useMemo(() => {
    return logs.filter(log => {
      if (levelFilter !== 'ALL' && log.level !== levelFilter) return false;
      if (moduleFilter !== 'ALL') {
        if (getModuleDisplay(log.module) !== moduleFilter) return false;
      }
      return true;
    });
  }, [logs, levelFilter, moduleFilter]);

  // Build items with time separators injected
  const renderItems = useMemo(() => {
    const items = [];
    let prevTime = 0;
    filteredLogs.forEach((log, index) => {
      const currTime = parseLogTime(log.timestamp);
      const gap = currTime - prevTime;
      // Insert separator if gap > 60 seconds (and not the first item)
      if (prevTime > 0 && gap > 60_000) {
        const mins = Math.round(gap / 60_000);
        items.push({ type: 'separator', key: `sep-${index}`, gap: mins });
      }
      items.push({ type: 'log', key: `log-${index}`, log, index });
      if (currTime > 0) prevTime = currTime;
    });
    return items;
  }, [filteredLogs]);

  const renderLogLine = (log, index) => {
    const displayModule = getModuleDisplay(log.module);

    let levelColorClass = 'text-gray-300';
    if (log.level === 'SUCCESS') levelColorClass = 'text-emerald-400 font-bold';
    if (log.level === 'WARNING') levelColorClass = 'text-yellow-400';
    if (log.level === 'ERROR') levelColorClass = 'text-red-500 font-bold';
    if (log.level === 'CRITICAL') levelColorClass = 'text-red-600 font-bold animate-pulse';

    let rowClass = 'py-0.5 hover:bg-gray-800/70 flex gap-3 border-b border-gray-800/40';
    if (log.level === 'ERROR' || log.level === 'CRITICAL') rowClass += ' bg-red-950/20';
    if (log.message?.includes('买入成功')) rowClass += ' bg-emerald-950/20';
    if (log.message?.includes('卖出成功')) rowClass += ' bg-rose-950/20';

    return (
      <div key={index} className={`font-mono text-sm ${rowClass}`}>
        <span className="text-gray-600 w-20 flex-shrink-0 select-none text-xs pt-0.5">{log.timestamp}</span>
        <span className={`w-20 flex-shrink-0 text-xs pt-0.5 ${levelColorClass}`}>[{log.level}]</span>
        <span className="text-cyan-500/80 w-14 flex-shrink-0 text-xs pt-0.5">[{displayModule}]</span>
        <span className="flex-1 break-all leading-relaxed">{highlightMessage(log.message)}</span>
      </div>
    );
  };

  return (
    <div className="flex flex-col h-full bg-gray-950 text-white overflow-hidden">
      {/* Control Bar */}
      <div className="bg-gray-900 px-4 py-3 border-b border-gray-700 flex flex-wrap items-center justify-between gap-3 shadow-md z-10">
        <div className="flex items-center gap-3">
          <TerminalIcon className="w-5 h-5 text-indigo-400" />
          <Title className="text-white text-base">系统日志</Title>
          <div className={`text-xs px-2 py-0.5 rounded-full ${isConnected ? 'bg-emerald-900/50 text-emerald-300 border border-emerald-700' : 'bg-red-900/50 text-red-300 border border-red-700'}`}>
            {isConnected ? '● 已连接' : '○ 断开'}
          </div>
          <span className="text-xs text-gray-500">{filteredLogs.length} 条</span>
        </div>

        <div className="flex items-center gap-2">
          <div className="w-32">
            <Select value={levelFilter} onValueChange={setLevelFilter}>
              <SelectItem value="ALL">全部级别</SelectItem>
              <SelectItem value="SUCCESS">SUCCESS</SelectItem>
              <SelectItem value="INFO">INFO</SelectItem>
              <SelectItem value="WARNING">WARNING</SelectItem>
              <SelectItem value="ERROR">ERROR</SelectItem>
            </Select>
          </div>
          <div className="w-32">
            <Select value={moduleFilter} onValueChange={setModuleFilter}>
              <SelectItem value="ALL">全部模块</SelectItem>
              <SelectItem value="监控">监控</SelectItem>
              <SelectItem value="分析">分析</SelectItem>
              <SelectItem value="交易">交易</SelectItem>
              <SelectItem value="持仓">持仓</SelectItem>
              <SelectItem value="API">API</SelectItem>
              <SelectItem value="系统">系统</SelectItem>
            </Select>
          </div>
          <div className="h-5 w-px bg-gray-700" />
          <Button size="xs" variant="secondary" icon={isPaused ? PlayIcon : PauseIcon}
            onClick={() => setIsPaused(!isPaused)} color={isPaused ? 'green' : 'amber'}>
            {isPaused ? '继续' : '暂停'}
          </Button>
          <Button size="xs" variant="secondary" icon={TrashIcon}
            onClick={() => setLogs([])} color="red">清空</Button>
        </div>
      </div>

      {/* Log Stream */}
      <div className="flex-1 overflow-y-auto px-3 py-2 bg-gray-950 font-mono">
        {renderItems.length === 0 ? (
          <div className="h-full flex items-center justify-center text-gray-700 text-sm">暂无日志...</div>
        ) : (
          <div>
            {renderItems.map((item) => {
              if (item.type === 'separator') {
                return (
                  <div key={item.key} className="flex items-center gap-3 my-2 py-1">
                    <div className="flex-1 h-px bg-gray-800" />
                    <span className="text-xs text-gray-600 px-2 py-0.5 rounded bg-gray-900 border border-gray-800">
                      ─ {item.gap} 分钟 ─
                    </span>
                    <div className="flex-1 h-px bg-gray-800" />
                  </div>
                );
              }
              return renderLogLine(item.log, item.index);
            })}
            <div ref={logsEndRef} />
          </div>
        )}
      </div>
    </div>
  );
};

export default Logs;
