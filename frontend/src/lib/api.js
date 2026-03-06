import axios from 'axios';

const API_URL = process.env.REACT_APP_BACKEND_URL || 'http://localhost:8000';

const api = axios.create({
  baseURL: `${API_URL}/api`,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Add auth token to requests
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('nba_edge_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Handle auth errors
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('nba_edge_token');
      localStorage.removeItem('nba_edge_user');
      window.location.href = '/login';
    }
    return Promise.reject(error);
  }
);

// Auth
export const authApi = {
  register: (data) => api.post('/auth/register', data),
  login: (data) => api.post('/auth/login', data),
  me: () => api.get('/auth/me'),
};

// Admin
export const adminApi = {
  syncHistorical: () => api.post('/admin/sync-historical'),
  buildFeatures: () => api.post('/admin/build-features'),
  train: () => api.post('/admin/train'),
  syncUpcoming: (days = 2) => api.post(`/admin/sync-upcoming?days=${days}`),
  syncOdds: (days = 2) => api.post(`/admin/sync-odds?days=${days}`),
  refreshResults: () => api.post('/admin/refresh-results'),
  captureClosingLines: (windowMinutes = 30) => api.post(`/admin/capture-closing-lines?window_minutes=${windowMinutes}`),
  runDailyPaper: () => api.post('/admin/run-daily-paper'),
  getClosingCaptureDiagnostics: () => api.get('/admin/diagnostics/closing-capture'),
  getPerformanceSummary: (days = 90) => api.get(`/admin/performance-summary?days=${days}`),
};

// User
export const userApi = {
  getUpcoming: () => api.get('/upcoming'),
  generatePicks: () => api.post('/picks/generate'),
  getPicks: () => api.get('/picks'),
  getHistory: (params) => api.get('/history', { params }),
  exportHistory: () => api.get('/history/export', { responseType: 'blob' }),
};

// Stats
export const statsApi = {
  getDataset: () => api.get('/stats/dataset'),
  getModel: () => api.get('/stats/model'),
};

export default api;
