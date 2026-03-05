import { useState, useEffect, useRef } from 'react';

const useWebSocket = (url, apiKey) => {
  const [data, setData] = useState(null);
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState(null);
  const wsRef = useRef(null);
  const reconnectTimeoutRef = useRef(null);

  useEffect(() => {
    let ws;
    
    const connect = () => {
      try {
        let wsUrl = url;
        // 如果不是 ws 开头，则根据 VITE_API_BASE 构造
        if (!url.startsWith('ws')) {
          const baseUrl = import.meta.env.VITE_API_BASE || 'http://localhost:8002';
          const wsBase = baseUrl.replace('http', 'ws');
          wsUrl = `${wsBase}${url}`;
        }

        // 添加 API Key 到 query parameters
        // 注意：WebSocket 标准 API 不支持 headers，只能通过 query params 或 subprotocol 传递鉴权信息
        // 这里假设后端支持 query param 'api_key'
        if (apiKey) {
           wsUrl += `${wsUrl.includes('?') ? '&' : '?'}api_key=${apiKey}`;
        }

        ws = new WebSocket(wsUrl);
        wsRef.current = ws;

        ws.onopen = () => {
          console.log(`WebSocket connected: ${wsUrl}`);
          setIsConnected(true);
          setError(null);
        };

        ws.onmessage = (event) => {
          try {
             const parsedData = JSON.parse(event.data);
             setData(parsedData);
          } catch (e) {
             // 如果不是JSON，可能是简单字符串
             setData(event.data);
          }
        };

        ws.onerror = (event) => {
          console.error(`WebSocket error (${wsUrl}):`, event);
          setError('WebSocket connection error');
          setIsConnected(false);
        };

        ws.onclose = () => {
          console.log(`WebSocket disconnected: ${wsUrl}`);
          setIsConnected(false);
          // 自动重连逻辑
          reconnectTimeoutRef.current = setTimeout(() => {
            console.log(`Attempting to reconnect: ${wsUrl}`);
            connect();
          }, 3000);
        };

      } catch (e) {
        console.error('WebSocket connection setup error:', e);
        setError(e.message);
      }
    };

    connect();

    return () => {
      // Prevent reconnect on unmount
      if (ws) {
        ws.onclose = null; // Remove handler to prevent reconnect trigger
        ws.close();
      }
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
    };
  }, [url, apiKey]); // 依赖项改变时重新连接

  const sendMessage = (message) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(message));
    } else {
      console.warn('Cannot send message, WebSocket not connected');
    }
  };

  return { data, isConnected, error, sendMessage };
};

export default useWebSocket;
