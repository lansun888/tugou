import React from 'react';
import { Badge } from '@tremor/react';

const StatusBadge = ({ status }) => {
  const getStatusConfig = (status) => {
    switch (status?.toLowerCase()) {
      case 'new':
      case 'analyzing':
        return { color: 'blue', text: '分析中' };
      case 'safe':
        return { color: 'emerald', text: '安全' };
      case 'risk':
      case 'scam':
        return { color: 'red', text: '高风险' };
      case 'bought':
      case 'open':
        return { color: 'green', text: '持仓中' };
      case 'sold':
      case 'closed':
        return { color: 'gray', text: '已平仓' };
      case 'rejected':
        return { color: 'rose', text: '已拒绝' };
      case 'pending':
        return { color: 'yellow', text: '待处理' };
      default:
        return { color: 'slate', text: status || 'Unknown' };
    }
  };

  const config = getStatusConfig(status);

  return (
    <Badge color={config.color}>
      {config.text}
    </Badge>
  );
};

export default StatusBadge;
