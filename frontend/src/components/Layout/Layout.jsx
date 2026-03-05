import React from 'react';
import { Outlet } from 'react-router-dom';
import Sidebar from './Sidebar';
import TopBar from './TopBar';

const Layout = () => {
  return (
    <div className="flex h-screen bg-gray-50 text-gray-900 font-sans">
      <Sidebar />
      <div className="flex-1 flex flex-col ml-64">
        <TopBar />
        <main className="flex-1 overflow-y-auto bg-gray-50">
          <Outlet />
        </main>
      </div>
    </div>
  );
};

export default Layout;
