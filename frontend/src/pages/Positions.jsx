import React, { useState, useEffect } from 'react';
import { Card, Text, Flex, Metric } from "@tremor/react";
import { Activity } from 'lucide-react';
import api from '../utils/api';
import useWebSocket from '../hooks/useWebSocket';
import SellModal from '../components/common/SellModal';
import PositionCard from '../components/PositionCard';

const Positions = () => {
  const [positions, setPositions] = useState([]);
  const [summary, setSummary] = useState({
    totalInvested: 0,
    currentValue: 0,
    totalPnL: 0,
    totalPnLPercent: 0
  });
  const [loading, setLoading] = useState(true);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [selectedPosition, setSelectedPosition] = useState(null);
  const [sellPercentage, setSellPercentage] = useState(0);

  // WebSocket for real-time price updates
  const apiKey = import.meta.env.VITE_API_KEY || 'tugou_secret_key';
  const { data: wsData } = useWebSocket('/ws/prices', apiKey);

  // Initial data fetch
  const fetchPositions = async () => {
    try {
      const res = await api.get('/positions');
      // Sort by buy_time descending
      const sortedRes = (res || []).sort((a, b) => (b.buy_time || 0) - (a.buy_time || 0));
      setPositions(sortedRes);
      calculateSummary(sortedRes);
    } catch (error) {
      console.error("Failed to fetch positions:", error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchPositions();
    // Fallback polling every 10s just in case WS fails or for initial sync
    const interval = setInterval(fetchPositions, 10000);
    return () => clearInterval(interval);
  }, []);

  // Handle WebSocket updates
  useEffect(() => {
    if (wsData && wsData.positions && Array.isArray(wsData.positions)) {
      // Update positions with real-time data
      const newPositions = wsData.positions;
      // Sort by buy_time descending (newest first)
      newPositions.sort((a, b) => (b.buy_time || 0) - (a.buy_time || 0));
      
      setPositions(newPositions);
      calculateSummary(newPositions);
    }
  }, [wsData]);

  const calculateSummary = (posList) => {
    const totalInvested = posList.reduce((acc, curr) => acc + (curr.invested_bnb || 0), 0);
    const currentValue = posList.reduce((acc, curr) => acc + (curr.current_value_bnb || 0), 0);
    const totalPnL = currentValue - totalInvested;
    const totalPnLPercent = totalInvested > 0 ? (totalPnL / totalInvested) * 100 : 0;

    setSummary({
      totalInvested,
      currentValue,
      totalPnL,
      totalPnLPercent
    });
  };

  const openSellModal = (position, percentage) => {
    setSelectedPosition(position);
    setSellPercentage(percentage);
    setIsModalOpen(true);
  };

  const handleSellConfirm = async () => {
    if (!selectedPosition) return;

    try {
      await api.post('/trade/sell', {
        token_address: selectedPosition.token_address,
        percentage: sellPercentage
      });
      // Refresh positions after sell
      fetchPositions();
      setIsModalOpen(false);
    } catch (error) {
      console.error("Sell failed:", error);
      alert("卖出失败: " + (error.response?.data?.detail || error.message));
    }
  };

  if (loading && positions.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-slate-400">
        <div className="flex flex-col items-center gap-4">
          <div className="w-8 h-8 border-4 border-indigo-500 border-t-transparent rounded-full animate-spin"></div>
          <p>加载持仓中...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6 p-6">
      <SellModal 
        isOpen={isModalOpen} 
        closeModal={() => setIsModalOpen(false)} 
        onConfirm={handleSellConfirm}
        tokenName={selectedPosition?.token_symbol}
        percentage={sellPercentage}
      />

      {/* 顶部汇总栏 */}
      <Card className="bg-slate-900 ring-slate-800">
        <Flex className="space-x-8 overflow-x-auto pb-2">
          <div className="min-w-[150px]">
            <Text className="text-slate-400 text-sm">总投资</Text>
            <Metric className="text-white text-xl mt-1">{summary.totalInvested.toFixed(4)} BNB</Metric>
          </div>
          <div className="w-px h-12 bg-slate-800 hidden sm:block"></div>
          <div className="min-w-[150px]">
            <Text className="text-slate-400 text-sm">当前价值</Text>
            <Metric className="text-white text-xl mt-1">{summary.currentValue.toFixed(4)} BNB</Metric>
          </div>
          <div className="w-px h-12 bg-slate-800 hidden sm:block"></div>
          <div className="min-w-[150px]">
            <Text className="text-slate-400 text-sm">总盈亏</Text>
            <Flex justifyContent="start" className="gap-2 mt-1">
              <Metric className={`text-xl ${summary.totalPnL >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                {summary.totalPnL >= 0 ? '+' : ''}{summary.totalPnL.toFixed(4)} BNB
              </Metric>
              <span className={`text-xl font-bold ${summary.totalPnL >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                {summary.totalPnL >= 0 ? '+' : ''}{summary.totalPnLPercent.toFixed(2)}%
              </span>
            </Flex>
          </div>
        </Flex>
      </Card>

      {/* 持仓列表 */}
      {positions.length === 0 ? (
        <div className="col-span-full flex flex-col items-center justify-center py-20 bg-slate-900/50 rounded-lg border border-slate-800 border-dashed">
            <div className="bg-slate-800 p-4 rounded-full mb-4">
              <Activity className="w-8 h-8 text-slate-500" />
            </div>
            <Text className="text-slate-400 text-lg">暂无活跃持仓</Text>
            <Text className="text-slate-600 text-sm mt-1">等待新的机会...</Text>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">
          {positions.map((pos) => (
            <PositionCard 
              key={pos.token_address} 
              pos={pos} 
              onSell={openSellModal} 
            />
          ))}
        </div>
      )}
    </div>
  );
};

export default Positions;