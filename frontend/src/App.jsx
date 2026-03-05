import React from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import Layout from './components/Layout/Layout';
import Dashboard from './pages/Dashboard';
import Discoveries from './pages/Discoveries';
import Positions from './pages/Positions';
import Trades from './pages/Trades';
import Settings from './pages/Settings';
import Logs from './pages/Logs';

function App() {
  return (
    <Router>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="discoveries" element={<Discoveries />} />
          <Route path="positions" element={<Positions />} />
          <Route path="trades" element={<Trades />} />
          <Route path="settings" element={<Settings />} />
          <Route path="logs" element={<Logs />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </Router>
  );
}

export default App;
