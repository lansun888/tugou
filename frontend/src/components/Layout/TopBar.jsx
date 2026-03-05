import React from 'react';
import { useLocation } from 'react-router-dom';
import { BellIcon, UserIcon } from 'lucide-react';
import { Button } from '@tremor/react';

const TopBar = () => {
  const location = useLocation();

  const getPageTitle = () => {
    switch (location.pathname) {
      case '/': return '仪表盘';
      case '/discoveries': return '新币发现';
      case '/positions': return '持仓管理';
      case '/trades': return '交易记录';
      case '/settings': return '系统设置';
      case '/logs': return '系统日志';
      default: return 'BSC Tugou Bot';
    }
  };

  return (
    <div className="h-16 bg-white border-b border-gray-200 flex items-center justify-between px-6 sticky top-0 z-10">
      <h1 className="text-xl font-bold text-gray-800">{getPageTitle()}</h1>
      
      <div className="flex items-center gap-4">
        <div className="relative">
          <Button variant="light" icon={BellIcon} className="text-gray-500 hover:text-gray-700 p-2 rounded-full" />
          <span className="absolute top-2 right-2 w-2 h-2 bg-red-500 rounded-full border border-white" />
        </div>
        
        <div className="flex items-center gap-2 border-l border-gray-200 pl-4 ml-2">
          <div className="w-8 h-8 bg-indigo-100 rounded-full flex items-center justify-center text-indigo-600">
            <UserIcon className="w-4 h-4" />
          </div>
          <div className="text-sm">
            <div className="font-medium text-gray-700">Admin</div>
            <div className="text-xs text-gray-500">Connected</div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default TopBar;
