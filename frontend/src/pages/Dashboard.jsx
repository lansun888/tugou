import React, { useState, useEffect } from 'react';
import { Card, Title, Text, Grid, Metric, Flex, ProgressBar, AreaChart, TabGroup, TabList, Tab, TabPanels, TabPanel, List, ListItem } from '@tremor/react';
import { useApi } from '../hooks/useApi';
import { formatNumber } from '../utils/formatters';
import { ActivityIcon, TrendingUpIcon, WalletIcon, AlertCircleIcon, Rocket, AlertTriangle, CheckCircle, GitCompareArrows } from 'lucide-react';

// 实盘 vs 模拟 对比卡片组件
const CompareCard = ({ liveStats, simStats }) => {
  if (!simStats?.stats) return null;
  // In simulation mode, use live_* fields (always from live tables); in live mode use the regular fields
  const isSimMode = liveStats?.mode === 'simulation';
  const liveProfit = isSimMode ? (liveStats?.live_today_profit_bnb ?? 0) : (liveStats?.today_profit_bnb ?? 0);
  const simProfit = simStats.stats.total_profit_bnb ?? 0;
  const liveWin = isSimMode ? (liveStats?.live_win_rate ?? 0) : (liveStats?.win_rate ?? 0);
  const simWin = simStats.stats.win_rate ?? 0;
  const liveTrades = isSimMode ? (liveStats?.live_today_trades ?? 0) : (liveStats?.today_trades ?? 0);
  const simTrades = simStats.stats.total_trades ?? 0;
  // 滑点损耗估算: 实盘收益 - 模拟收益 (仅在有实盘数据时有意义)
  const slippageLoss = simProfit > 0 ? ((simProfit - liveProfit) / simProfit * 100) : null;

  const Row = ({ label, live, sim, liveClass, simClass, note }) => (
    <div className="grid grid-cols-3 items-center py-2 border-b border-gray-100 last:border-0">
      <span className="text-sm text-gray-500">{label}</span>
      <span className={`text-center font-semibold ${liveClass || 'text-gray-900'}`}>{live}</span>
      <span className={`text-center font-semibold ${simClass || 'text-gray-900'}`}>{sim}</span>
    </div>
  );

  return (
    <Card>
      <Flex justifyContent="start" className="gap-2 mb-4">
        <GitCompareArrows className="w-5 h-5 text-indigo-500" />
        <Title>今日实盘 vs 模拟对比</Title>
      </Flex>
      <div className="grid grid-cols-3 text-xs text-gray-400 font-medium pb-1 border-b border-gray-200">
        <span>指标</span>
        <span className="text-center text-blue-600">实盘</span>
        <span className="text-center text-purple-600">模拟</span>
      </div>
      <Row label="净盈亏 (BNB)"
        live={`${liveProfit >= 0 ? '+' : ''}${formatNumber(liveProfit, 4)}`}
        sim={`${simProfit >= 0 ? '+' : ''}${formatNumber(simProfit, 4)}`}
        liveClass={liveProfit >= 0 ? 'text-emerald-600' : 'text-rose-600'}
        simClass={simProfit >= 0 ? 'text-emerald-600' : 'text-rose-600'}
      />
      <Row label="胜率"
        live={`${formatNumber(liveWin, 1)}%`}
        sim={`${formatNumber(simWin, 1)}%`}
        liveClass={liveWin >= 50 ? 'text-emerald-600' : 'text-rose-600'}
        simClass={simWin >= 50 ? 'text-emerald-600' : 'text-rose-600'}
      />
      <Row label="交易次数"
        live={liveTrades}
        sim={simTrades}
      />
      {slippageLoss !== null && (
        <div className="mt-3 p-3 rounded-lg bg-amber-50 border border-amber-100">
          <Text className="text-xs text-amber-700">
            📊 估算滑点/摩擦损耗: <span className="font-bold">{formatNumber(slippageLoss, 1)}%</span>
            （模拟盈利 - 实盘盈利 差值，供参考）
          </Text>
        </div>
      )}
    </Card>
  );
};

const Dashboard = () => {
  const { data: stats, loading: statsLoading } = useApi('/status', 5000);
  const { data: dailyStats, loading: chartLoading } = useApi('/stats/daily?days=7', 60000);
  const { data: simData, loading: simLoading, error: simError } = useApi('/simulation/stats?days=7', 60000);

  const chartData = (Array.isArray(dailyStats) ? dailyStats : []).map(item => ({
    date: item.day,
    "盈亏 (BNB)": parseFloat((item.total_pnl_bnb ?? 0).toFixed(6))
  }));

  if (statsLoading && !stats) return <div className="p-6">加载中...</div>;

  return (
    <div className="p-6 space-y-6">
      <div>
        <Title>仪表盘</Title>
        <Text>实时概览与核心指标</Text>
      </div>

      <TabGroup>
        <TabList>
            <Tab>实盘概览</Tab>
            <Tab>模拟统计</Tab>
        </TabList>
        <TabPanels>
            <TabPanel>
              <div className="mt-6 space-y-6">
                <Grid numItems={1} numItemsSm={2} numItemsLg={4} className="gap-6">
                    <Card decoration="top" decorationColor="indigo">
                    <Flex justifyContent="start" className="space-x-4">
                        <WalletIcon className="w-8 h-8 text-indigo-500" />
                        <div className="flex-1">
                        <Text>BNB 余额</Text>
                        <Metric>{formatNumber(stats?.bnb_balance || 0, 4)} BNB</Metric>
                        {stats?.initial_balance != null && (
                          <Text className="text-xs text-gray-400 mt-1">
                            初始: {formatNumber(stats.initial_balance, 4)} BNB &nbsp;|&nbsp;
                            <span className={(stats.bnb_balance - stats.initial_balance) >= 0 ? 'text-emerald-500' : 'text-rose-500'}>
                              总变化: {(stats.bnb_balance - stats.initial_balance) >= 0 ? '+' : ''}
                              {formatNumber(stats.bnb_balance - stats.initial_balance, 4)} BNB
                            </span>
                          </Text>
                        )}
                        </div>
                    </Flex>
                    </Card>
                    <Card decoration="top" decorationColor="emerald">
                    <Flex justifyContent="start" className="space-x-4">
                        <TrendingUpIcon className="w-8 h-8 text-emerald-500" />
                        <div>
                        <Text>今日盈亏</Text>
                        <Metric className={stats?.today_profit_bnb >= 0 ? 'text-emerald-600' : 'text-rose-600'}>
                            {stats?.today_profit_bnb > 0 ? '+' : ''}{formatNumber(stats?.today_profit_bnb || 0, 4)} BNB
                        </Metric>
                        </div>
                    </Flex>
                    </Card>
                    <Card decoration="top" decorationColor="amber">
                    <Flex justifyContent="start" className="space-x-4">
                        <ActivityIcon className="w-8 h-8 text-amber-500" />
                        <div>
                        <Text>当前持仓</Text>
                        <Metric>{stats?.active_positions || 0}</Metric>
                        </div>
                    </Flex>
                    </Card>
                    <Card decoration="top" decorationColor="rose">
                    <Flex justifyContent="start" className="space-x-4">
                        <AlertCircleIcon className="w-8 h-8 text-rose-500" />
                        <div>
                        <Text>今日胜率</Text>
                        <Metric>{formatNumber(stats?.win_rate || 0, 1)}%</Metric>
                        </div>
                    </Flex>
                    </Card>
                </Grid>

                <Grid numItems={1} className="gap-6">
                    <Card>
                    <Title>盈亏趋势 (7天)</Title>
                    <AreaChart
                        className="h-72 mt-4"
                        data={chartData}
                        index="date"
                        categories={["盈亏 (BNB)"]}
                        colors={["emerald"]}
                        valueFormatter={(number) => `${formatNumber(number, 4)} BNB`}
                    />
                    </Card>
                </Grid>

                {/* Live vs Simulation compare */}
                {simData && !simError && (
                  <CompareCard liveStats={stats} simStats={simData} />
                )}
              </div>
            </TabPanel>
            
            <TabPanel>
                <div className="mt-6 space-y-6">
                    {/* Simulation Stats */}
                    {simLoading && !simData ? (
                        <div className="text-center py-10">加载模拟数据中...</div>
                    ) : !simData || simData.error || simError ? (
                        <Card>
                            <Flex justifyContent="center" className="py-10 flex-col gap-4">
                                <AlertTriangle className="w-12 h-12 text-amber-500" />
                                <Text>无法加载模拟数据 (可能未启用模拟模式)</Text>
                                <Text className="text-xs text-slate-500">
                                    {simError?.message || simData?.error || "Unknown Error"}
                                </Text>
                                {simError?.response?.data?.error && (
                                    <Text className="text-xs text-rose-500">
                                        Server Error: {simError.response.data.error}
                                    </Text>
                                )}
                            </Flex>
                        </Card>
                    ) : (
                        <>
                            {/* Analysis & Suggestion */}
                            <Card decoration="top" decorationColor={
                                simData.analysis.action === 'switch_to_live' ? 'emerald' : 
                                simData.analysis.action === 'warning' ? 'rose' : 'amber'
                            }>
                                <Flex justifyContent="start" alignItems="start" className="gap-4">
                                    {simData.analysis.action === 'switch_to_live' ? (
                                        <Rocket className="w-8 h-8 text-emerald-500 mt-1" />
                                    ) : simData.analysis.action === 'warning' ? (
                                        <AlertTriangle className="w-8 h-8 text-rose-500 mt-1" />
                                    ) : (
                                        <ActivityIcon className="w-8 h-8 text-amber-500 mt-1" />
                                    )}
                                    <div className="flex-1">
                                        <Title>系统建议: {
                                            simData.analysis.action === 'switch_to_live' ? '建议实盘' : 
                                            simData.analysis.action === 'warning' ? '警告' : '继续观察'
                                        }</Title>
                                        <Text className="mt-2 text-slate-300">{simData.analysis.message}</Text>
                                        
                                        {simData.analysis.suggestions && simData.analysis.suggestions.length > 0 && (
                                            <div className="mt-4 bg-slate-800 p-4 rounded-lg">
                                                <Text className="font-bold mb-2">优化建议:</Text>
                                                <List>
                                                    {simData.analysis.suggestions.map((s, idx) => (
                                                        <ListItem key={idx}>
                                                            <span>• {s}</span>
                                                        </ListItem>
                                                    ))}
                                                </List>
                                            </div>
                                        )}
                                    </div>
                                </Flex>
                            </Card>

                            <Grid numItems={1} numItemsSm={2} numItemsLg={4} className="gap-6">
                                <Card>
                                    <Text>总交易数 (7天)</Text>
                                    <Metric>{simData.stats.total_trades}</Metric>
                                </Card>
                                <Card>
                                    <Text>模拟胜率</Text>
                                    <Metric className={simData.stats.win_rate > 50 ? 'text-emerald-500' : 'text-rose-500'}>
                                        {simData.stats.win_rate}%
                                    </Metric>
                                </Card>
                                <Card>
                                    <Text>总盈亏 (BNB)</Text>
                                    <Metric className={simData.stats.total_profit_bnb > 0 ? 'text-emerald-500' : 'text-rose-500'}>
                                        {simData.stats.total_profit_bnb > 0 ? '+' : ''}{simData.stats.total_profit_bnb}
                                    </Metric>
                                </Card>
                                <Card>
                                    <Text>期望值 (EV)</Text>
                                    <Metric>{simData.stats.expected_value}</Metric>
                                </Card>
                            </Grid>
                            
                            <Grid numItems={1} numItemsSm={2} className="gap-6">
                                <Card>
                                    <Title>盈利分布</Title>
                                    <List className="mt-4">
                                        <ListItem>
                                            <span>盈利交易</span>
                                            <span className="font-semibold text-emerald-600">{simData.stats.win_count} 次</span>
                                        </ListItem>
                                        <ListItem>
                                            <span>亏损交易</span>
                                            <span className="font-semibold text-rose-600">{simData.stats.loss_count} 次</span>
                                        </ListItem>
                                    </List>
                                </Card>
                                <Card>
                                    <Title>平均表现</Title>
                                    <List className="mt-4">
                                        <ListItem>
                                            <span>平均盈利</span>
                                            <Text className="text-emerald-500">+{simData.stats.avg_profit_bnb} BNB</Text>
                                        </ListItem>
                                        <ListItem>
                                            <span>平均亏损</span>
                                            <Text className="text-rose-500">{simData.stats.avg_loss_bnb} BNB</Text>
                                        </ListItem>
                                    </List>
                                </Card>
                            </Grid>
                        </>
                    )}
                </div>
            </TabPanel>
        </TabPanels>
      </TabGroup>
    </div>
  );
};

export default Dashboard;
