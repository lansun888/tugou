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

export const formatPrice = (price) => {
  if (!price || price === 0) return '0.00000000';
  
  const num = Number(price);
  if (isNaN(num)) return '0.00000000';

  // 统一显示8位有效小数，去掉末尾多余的0 
  if (num < 0.00000001) return num.toExponential(4);  // 极小值才用科学计数 
  
  // 找到第一个非零位，然后保留后面6位有效数字 
  const str = num.toFixed(12);
  const match = str.match(/0\.(0*)([1-9].{0,7})/);
  
  if (match) { 
    const zeros = match[1];
    // match[2] is the significant part starting with non-zero.
    // User wants "保留后面6位有效数字" (keep 6 significant digits).
    const significant = match[2].slice(0, 6);
    return '0.' + zeros + significant;
  } 
  
  // Fallback
  return num.toFixed(8).replace(/\.?0+$/, '');
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
