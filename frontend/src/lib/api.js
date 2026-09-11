const API_BASE = '/api/v1';

function getToken() {
  return localStorage.getItem('token');
}

function headers() {
  const h = { 'Content-Type': 'application/json' };
  const t = getToken();
  if (t) h['Authorization'] = `Bearer ${t}`;
  return h;
}

async function api(path, opts = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    ...opts,
    headers: { ...headers(), ...(opts.headers || {}) },
  });
  if (res.status === 401) {
    localStorage.removeItem('token');
    window.location.href = '/login';
    return;
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export const auth = {
  login: (email, password) =>
    api('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),
};

export const dashboard = {
  getData: (period = 'today', marketplace = 'all', startDate = null, endDate = null) => {
    let url = `/dashboard/data?period=${period}&marketplace=${marketplace}`;
    if (startDate) url += `&start_date=${startDate}`;
    if (endDate) url += `&end_date=${endDate}`;
    return api(url);
  },
  getAbc: (period = 'today', marketplace = 'all', startDate = null, endDate = null) => {
    let url = `/dashboard/abc?period=${period}&marketplace=${marketplace}`;
    if (startDate) url += `&start_date=${startDate}`;
    if (endDate) url += `&end_date=${endDate}`;
    return api(url);
  },
};

export const reports = {
  preview: (payload) => api('/reports/preview', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  abc: (month) => api(`/reports/abc-classification?month=${month}`),
  reconciliation: (month) => api(`/reports/reconciliation?month=${month}`),
  getTarget: (month) => api(`/reports/monthly-targets/${month}`),
  saveTarget: (month, data) => api(`/reports/monthly-targets/${month}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  }),
  planFact: (month) => api(`/reports/plan-fact?month=${month}`),
  download: async (payload) => {
    const res = await fetch(`${API_BASE}/reports/pdf`, {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify(payload),
    });
    if (res.status === 401) {
      localStorage.removeItem('token');
      window.location.href = '/login';
      return;
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.blob();
  },
};

export const products = {
  list: () => api('/products'),
  updateCost: (sku, costPrice) =>
    api(`/products/${sku}/cost`, {
      method: 'PUT',
      body: JSON.stringify({ cost_price: costPrice }),
    }),
  merge: (sourceSkus, targetSku) =>
    api('/products/merge', {
      method: 'POST',
      body: JSON.stringify({ source_skus: sourceSkus, target_sku: targetSku }),
    }),
  priceHistory: (productId, dateFrom = null, dateTo = null) => {
    const params = new URLSearchParams();
    if (dateFrom) params.set('date_from', dateFrom);
    if (dateTo) params.set('date_to', dateTo);
    const qs = params.toString();
    return api(`/products/${productId}/price-history${qs ? `?${qs}` : ''}`);
  },
};

export const shops = {
  list: () => api('/shops/'),
  sync: (shopId) =>
    api(`/shops/${shopId}/sync`, {
      method: 'POST',
    }),
  toggleSync: (shopId) =>
    api(`/shops/${shopId}/toggle-sync`, {
      method: 'PUT',
    }),
  syncLogs: (shopId, limit = 1) => api(`/shops/${shopId}/sync-logs?limit=${limit}`),
};

export const balances = {
  list: () => api('/balances/'),
};