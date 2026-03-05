import { useState, useEffect, useCallback } from 'react';
import api from '../utils/api';

/**
 * 
 * @param {string} endpoint API endpoint
 * @param {number} interval Polling interval in ms (0 for no polling)
 * @param {object} initialData Initial data
 * @returns {object} { data, loading, error, refresh }
 */
export const useApi = (endpoint, interval = 0, initialData = null) => {
  const [data, setData] = useState(initialData);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      const result = await api.get(endpoint);
      setData(result);
      setError(null);
    } catch (err) {
      setError(err);
      console.error(`Error fetching ${endpoint}:`, err);
    } finally {
      setLoading(false);
    }
  }, [endpoint]);

  useEffect(() => {
    fetchData();
    
    if (interval > 0) {
      const timer = setInterval(fetchData, interval);
      return () => clearInterval(timer);
    }
  }, [fetchData, interval]);

  return { data, loading, error, refresh: fetchData };
};

export default useApi;
