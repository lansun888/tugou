import React from 'react';
import { NavLink } from 'react-router-dom';
import { 
  HomeIcon, 
  SearchIcon, 
  BriefcaseIcon, 
  HistoryIcon, 
  SettingsIcon, 
  TerminalIcon 
} from 'lucide-react';

const Sidebar = () => {
  const navItems = [
    { name: '仪表盘', path: '/', icon: HomeIcon },
    { name: '新币发现', path: '/discoveries', icon: SearchIcon },
    { name: '持仓管理', path: '/positions', icon: BriefcaseIcon },
    { name: '交易记录', path: '/trades', icon: HistoryIcon },
    { name: '系统设置', path: '/settings', icon: SettingsIcon },
    { name: '系统日志', path: '/logs', icon: TerminalIcon },
  ];

  return (
    <div className="w-64 h-screen bg-gray-900 text-white flex flex-col fixed left-0 top-0 border-r border-gray-800">
      <div className="p-6 border-b border-gray-800 flex items-center gap-3">
        <div className="w-8 h-8 bg-indigo-500 rounded-lg flex items-center justify-center font-bold text-xl">
          🚀
        </div>
        <span className="font-bold text-lg tracking-wider">BSC TUGOU</span>
      </div>

      <nav className="flex-1 p-4 space-y-2 overflow-y-auto">
        {navItems.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            className={({ isActive }) =>
              `flex items-center gap-3 px-4 py-3 rounded-lg transition-colors ${
                isActive
                  ? 'bg-indigo-600 text-white'
                  : 'text-gray-400 hover:bg-gray-800 hover:text-white'
              }`
            }
          >
            <item.icon className="w-5 h-5" />
            <span className="font-medium">{item.name}</span>
          </NavLink>
        ))}
      </nav>

      <div className="p-4 border-t border-gray-800">
        <div className="bg-gray-800 rounded-lg p-3">
          <div className="text-xs text-gray-500 mb-1">运行状态</div>
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
            <span className="text-sm font-medium text-green-400">系统正常</span>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Sidebar;
