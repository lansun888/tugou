import React, { useState, useEffect } from 'react';
import { Card, Title, Text, Button, NumberInput, TextInput, Select, SelectItem, Switch } from '@tremor/react';
import api from '../utils/api';
import { SaveIcon, SettingsIcon, ShieldIcon, ActivityIcon, BellIcon } from 'lucide-react';

const Settings = () => {
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [isDirty, setIsDirty] = useState(false);
  const [activeTab, setActiveTab] = useState(0);

  useEffect(() => {
    fetchConfig();
  }, []);

  const fetchConfig = async () => {
    setLoading(true);
    try {
      const data = await api.get('/config');
      if (data) {
        // Ensure default values for missing keys
        if (!data.monitor) data.monitor = {};
        if (!data.monitor.dex_enabled) data.monitor.dex_enabled = { pancakeswap_v2: true, pancakeswap_v3: true, biswap: true };
        if (!data.position_management) data.position_management = {};
        if (!data.position_management.daily_risk) data.position_management.daily_risk = {};
        if (!data.position_management.take_profit) data.position_management.take_profit = { levels: [] };
        if (!data.notifications) data.notifications = { enable_telegram: false, telegram_token: '', telegram_chat_id: '' };
        
        setConfig(data);
        setIsDirty(false);
      }
    } catch (error) {
      console.error('Failed to fetch config:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.post('/config', { config });
      setIsDirty(false);
      // Show success message (could be a toast, but using alert for now as requested)
      // alert('已保存✓'); 
    } catch (error) {
      console.error('Failed to save config:', error);
      alert('保存失败: ' + error.message);
    } finally {
      setSaving(false);
    }
  };

  const updateConfig = (path, value) => {
    setConfig(prev => {
      const newConfig = JSON.parse(JSON.stringify(prev));
      const keys = path.split('.');
      let current = newConfig;
      for (let i = 0; i < keys.length - 1; i++) {
        if (!current[keys[i]]) current[keys[i]] = {};
        current = current[keys[i]];
      }
      current[keys[keys.length - 1]] = value;
      return newConfig;
    });
    setIsDirty(true);
  };

  // Helper for Take Profit Levels
  const updateTakeProfitLevel = (index, field, value) => {
    const levels = [...(config.position_management?.take_profit?.levels || [])];
    if (!levels[index]) levels[index] = [0, 0];
    
    // field 0 = target %, field 1 = sell %
    levels[index][field] = Number(value);
    updateConfig('position_management.take_profit.levels', levels);
  };

  if (loading) return <div className="p-6 flex justify-center"><Text>加载配置中...</Text></div>;
  if (!config) return <div className="p-6 flex justify-center"><Text>无法加载配置</Text></div>;

  const categories = [
    { name: '交易参数', icon: ActivityIcon },
    { name: '风险控制', icon: ShieldIcon },
    { name: 'DEX设置', icon: SettingsIcon },
    { name: '通知设置', icon: BellIcon },
  ];

  return (
    <div className="flex h-full bg-gray-50">
      {/* Sidebar Menu */}
      <div className="w-64 bg-white border-r border-gray-200 flex flex-col">
        <div className="p-6 border-b border-gray-100">
          <Title>系统设置</Title>
          <Text className="text-xs mt-1">v1.0.0</Text>
        </div>
        <nav className="flex-1 p-4 space-y-1">
          {categories.map((cat, index) => (
            <button
              key={cat.name}
              onClick={() => setActiveTab(index)}
              className={`w-full flex items-center px-4 py-3 text-sm font-medium rounded-md transition-colors ${
                activeTab === index 
                  ? 'bg-blue-50 text-blue-700' 
                  : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
              }`}
            >
              <cat.icon className="mr-3 h-5 w-5" />
              {cat.name}
            </button>
          ))}
        </nav>
        
        {/* Save Status Footer */}
        <div className="p-4 border-t border-gray-200 bg-gray-50">
          <div className="flex items-center justify-between mb-3">
            <Text className="text-sm font-medium">状态</Text>
            {isDirty ? (
              <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-yellow-100 text-yellow-800">
                未保存
              </span>
            ) : (
              <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-green-100 text-green-800">
                已保存✓
              </span>
            )}
          </div>
          <Button 
            className="w-full" 
            icon={SaveIcon} 
            loading={saving} 
            onClick={handleSave}
            disabled={!isDirty}
            variant={isDirty ? "primary" : "secondary"}
          >
            {isDirty ? "保存修改" : "已保存"}
          </Button>
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 overflow-y-auto p-8">
        <div className="max-w-3xl mx-auto space-y-6">
          
          {/* Header */}
          <div className="mb-6">
            <Title>{categories[activeTab].name}</Title>
            <Text>配置机器人{categories[activeTab].name}相关选项</Text>
          </div>

          {/* 1. Transaction Parameters */}
          {activeTab === 0 && (
            <div className="space-y-6">
              <Card>
                <Title className="mb-4">基础交易设置</Title>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  <div>
                    <Text className="mb-1">每次买入金额 (BNB)</Text>
                    <NumberInput 
                      value={config.trading?.buy_amount || 0.01} 
                      onValueChange={(v) => updateConfig('trading.buy_amount', v)}
                      step={0.01}
                      min={0.01}
                      max={10}
                    />
                    <Text className="text-xs text-gray-400 mt-1">范围: 0.01 - 10 BNB</Text>
                  </div>
                  <div>
                    <Text className="mb-1">默认滑点 (%)</Text>
                    <div className="flex items-center space-x-4">
                      <input 
                        type="range" 
                        min="1" 
                        max="25" 
                        value={config.trading?.slippage || 10} 
                        onChange={(e) => updateConfig('trading.slippage', Number(e.target.value))}
                        className="flex-1 h-2 bg-gray-200 rounded-lg appearance-none cursor-pointer"
                      />
                      <span className="font-mono font-medium w-12 text-right">{config.trading?.slippage}%</span>
                    </div>
                  </div>
                  <div>
                    <Text className="mb-1">Gas 价格策略</Text>
                    <Select 
                      value={
                        config.trading?.gas?.mode === 'frontrun' ? 'fast' : 
                        (config.trading?.gas?.normal_multiplier > 1.15 ? 'normal' : 'saver')
                      }
                      onValueChange={(v) => {
                        if (v === 'saver') {
                          updateConfig('trading.gas.mode', 'normal');
                          updateConfig('trading.gas.normal_multiplier', 1.1);
                        } else if (v === 'normal') {
                          updateConfig('trading.gas.mode', 'normal');
                          updateConfig('trading.gas.normal_multiplier', 1.2);
                        } else {
                          updateConfig('trading.gas.mode', 'frontrun');
                          updateConfig('trading.gas.frontrun_multiplier', 1.5);
                        }
                      }}
                    >
                      <SelectItem value="saver">省钱模式 (1.1x)</SelectItem>
                      <SelectItem value="normal">正常模式 (1.2x)</SelectItem>
                      <SelectItem value="fast">抢跑模式 (1.5x)</SelectItem>
                    </Select>
                  </div>
                  <div>
                    <Text className="mb-1">买入等待时间 (秒)</Text>
                    <NumberInput 
                      value={config.monitor?.observation_wait_time || 30} 
                      onValueChange={(v) => updateConfig('monitor.observation_wait_time', v)}
                      step={1}
                      min={0}
                    />
                  </div>
                </div>
              </Card>

              <Card>
                <Title className="mb-4">分批止盈配置 (Take Profit)</Title>
                <Text className="mb-6">设置不同涨幅阶段的自动卖出比例</Text>
                
                {/* Visual Segmented Bar */}
                <div className="mb-8 relative h-12 bg-gray-100 rounded-lg overflow-hidden flex border border-gray-200">
                  {(config.position_management?.take_profit?.levels || []).map((level, index) => (
                    <div 
                      key={index}
                      className={`h-full flex flex-col justify-center items-center text-xs font-medium transition-all duration-300 ${
                        index === 0 ? 'bg-green-100 text-green-800' :
                        index === 1 ? 'bg-green-200 text-green-900' :
                        index === 2 ? 'bg-green-300 text-green-900' :
                        'bg-green-400 text-white'
                      }`}
                      style={{ width: '25%' }} // Fixed width for visualization as requested
                    >
                      <span>+{level[0]}%</span>
                      <span>卖{level[1]}%</span>
                    </div>
                  ))}
                </div>

                <div className="space-y-4">
                  {(config.position_management?.take_profit?.levels || []).map((level, index) => (
                    <div key={index} className="flex items-center space-x-4 bg-gray-50 p-3 rounded-md">
                      <div className="w-8 h-8 rounded-full bg-blue-100 flex items-center justify-center text-blue-600 font-bold text-sm">
                        {index + 1}
                      </div>
                      <div className="flex-1 grid grid-cols-2 gap-4">
                        <div>
                          <Text className="text-xs mb-1">目标倍数/涨幅 (%)</Text>
                          <NumberInput 
                            value={level[0]} 
                            onValueChange={(v) => updateTakeProfitLevel(index, 0, v)}
                            step={10}
                          />
                        </div>
                        <div>
                          <Text className="text-xs mb-1">卖出比例 (%)</Text>
                          <NumberInput 
                            value={level[1]} 
                            onValueChange={(v) => updateTakeProfitLevel(index, 1, v)}
                            step={5}
                            max={100}
                          />
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </Card>
            </div>
          )}

          {/* 2. Risk Control */}
          {activeTab === 1 && (
            <Card>
              <Title className="mb-6">风险控制参数</Title>
              <div className="space-y-6">
                <div className="flex items-center justify-between p-4 bg-red-50 rounded-lg border border-red-100">
                  <div>
                    <Text className="font-medium text-red-900">每日最大亏损 (BNB)</Text>
                    <Text className="text-xs text-red-700">达到此亏损额后自动停止所有买入</Text>
                  </div>
                  <div className="w-32">
                    <NumberInput 
                      value={config.position_management?.daily_risk?.max_daily_loss || 0.5} 
                      onValueChange={(v) => updateConfig('position_management.daily_risk.max_daily_loss', v)}
                      step={0.1}
                    />
                  </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  <div>
                    <Text className="mb-1">最低安全评分 (0-100)</Text>
                    <NumberInput 
                      value={config.monitor?.min_security_score || 80} 
                      onValueChange={(v) => updateConfig('monitor.min_security_score', v)}
                      max={100}
                      min={0}
                    />
                    <Text className="text-xs text-gray-400 mt-1">低于此分数的代币将被过滤</Text>
                  </div>
                  <div>
                    <Text className="mb-1">最大同时持仓数</Text>
                    <NumberInput 
                      value={config.position_management?.max_concurrent_positions || 5} 
                      onValueChange={(v) => updateConfig('position_management.max_concurrent_positions', v)}
                      step={1}
                      min={1}
                    />
                  </div>
                  <div>
                    <Text className="mb-1">单币最大亏损比例 (%)</Text>
                    <NumberInput 
                      value={Math.abs(config.position_management?.trailing_stop?.initial_stop_loss || 50)} 
                      onValueChange={(v) => updateConfig('position_management.trailing_stop.initial_stop_loss', -Math.abs(v))}
                      step={5}
                      min={5}
                      max={90}
                    />
                    <Text className="text-xs text-gray-400 mt-1">触发止损的跌幅阈值</Text>
                  </div>
                </div>
              </div>
            </Card>
          )}

          {/* 3. DEX Settings */}
          {activeTab === 2 && (
            <Card>
              <Title className="mb-6">DEX 交易所配置</Title>
              <div className="space-y-4">
                {Object.entries(config.monitor?.dex_enabled || {}).map(([dex, enabled]) => (
                  <div key={dex} className="flex items-center justify-between p-4 bg-white border border-gray-200 rounded-lg">
                    <div className="flex items-center space-x-3">
                      <div className={`w-2 h-2 rounded-full ${enabled ? 'bg-green-500' : 'bg-gray-300'}`} />
                      <Text className="font-medium capitalize">{dex.replace('_', ' ')}</Text>
                    </div>
                    <Switch 
                      checked={enabled} 
                      onChange={(v) => updateConfig(`monitor.dex_enabled.${dex}`, v)}
                    />
                  </div>
                ))}
              </div>
            </Card>
          )}

          {/* 4. Notification Settings */}
          {activeTab === 3 && (
            <Card>
              <Title className="mb-6">通知设置</Title>
              <div className="space-y-6">
                <div className="flex items-center justify-between">
                  <Text>启用 Telegram 通知</Text>
                  <Switch 
                    checked={config.notifications?.enable_telegram || false} 
                    onChange={(v) => updateConfig('notifications.enable_telegram', v)}
                  />
                </div>
                <div>
                  <Text className="mb-1">Telegram Bot Token</Text>
                  <TextInput 
                    type="password"
                    value={config.notifications?.telegram_token || ''} 
                    onValueChange={(v) => updateConfig('notifications.telegram_token', v)}
                    placeholder="123456789:ABCdef..."
                  />
                </div>
                <div>
                  <Text className="mb-1">Chat ID</Text>
                  <TextInput 
                    value={config.notifications?.telegram_chat_id || ''} 
                    onValueChange={(v) => updateConfig('notifications.telegram_chat_id', v)}
                    placeholder="-100123456789"
                  />
                </div>
              </div>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
};

export default Settings;
