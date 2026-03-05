export const formatNumber = (value, decimals = 4) => {
  if (value === undefined || value === null) return '--';
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: decimals });
};

// 数字格式化
export const formatBNB = (value) => {
  if (value === undefined || value === null) return '--';
  return `${Number(value).toFixed(4)} BNB`;
};

export const formatUSD = (value) => {
  if (value === undefined || value === null) return '--';
  return `$${Number(value).toFixed(2)}`;
};

export const formatPercent = (value) => {
  if (value === undefined || value === null) return '--';
  const num = Number(value);
  const sign = num >= 0 ? '+' : '';
  return `${sign}${num.toFixed(2)}%`;
};

export const formatPrice = (value) => {
  if (value === undefined || value === null) return '--';
  const num = Number(value);
  if (isNaN(num)) return '--';
  if (num === 0) return '0.00';
  if (num < 0.0000001) return num.toExponential(4);
  if (num < 0.001) return num.toFixed(8);
  if (num < 1) return num.toFixed(6);
  if (num < 1000) return num.toFixed(4);
  return num.toFixed(2);
};

// 日期格式化
export const formatDate = (dateString) => {
  if (!dateString) return '--';
  const date = new Date(dateString);
  return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
};

export const formatTime = (dateString) => {
  if (!dateString) return '--';
  const date = new Date(dateString);
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
};

export const formatTimeAgo = (dateString) => {
  if (!dateString || dateString === 0 || dateString === '0') return '--';
  
  let past;
  const timestamp = Number(dateString);
  // Check if it's a timestamp in seconds (less than year 5138 in seconds)
  // 1e11 seconds is year 5138. 
  // 1e11 ms is year 1973.
  // So if it's < 1e11, it's definitely seconds (unless it's a very old date from 1970-1973 in ms, but we assume recent dates).
  if (!isNaN(timestamp) && timestamp < 100000000000) {
     past = new Date(timestamp * 1000);
  } else {
     past = new Date(dateString);
  }

  // Validate date
  if (isNaN(past.getTime())) return '--';
  
  // If date is too old (e.g. 1970), return '--'
  if (past.getFullYear() < 2020) return '--';

  const now = new Date();
  const diffMs = now - past;
  const diffHrs = Math.floor(diffMs / (1000 * 60 * 60));
  const diffMins = Math.floor((diffMs % (1000 * 60 * 60)) / (1000 * 60));
  
  if (diffHrs > 24) return `${Math.floor(diffHrs / 24)}天前`;
  if (diffHrs > 0) return `${diffHrs}小时 ${diffMins}分钟前`;
  return `${diffMins}分钟前`;
};
