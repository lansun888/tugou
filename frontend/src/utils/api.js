import axios from 'axios';

// Vite 使用 import.meta.env
// Use relative path to leverage Vite proxy
const BASE_URL = import.meta.env.VITE_API_BASE || '';
// Append /api to the base URL since all endpoints are prefixed with /api
const API_BASE = `${BASE_URL}/api`;
const API_KEY = import.meta.env.VITE_API_KEY || 'tugou_secret_key';

console.log('API Config:', { BASE_URL, API_BASE, API_KEY });

const api = axios.create({
  baseURL: API_BASE,
  timeout: 10000,
  headers: {
    'Content-Type': 'application/json',
    'X-API-Key': API_KEY
  }
});

// 请求拦截器
api.interceptors.request.use(
  (config) => {
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// 响应拦截器
api.interceptors.response.use(
  (response) => {
    return response.data;
  },
  (error) => {
    console.error('API Error:', error);
    return Promise.reject(error);
  }
);

export default api;
