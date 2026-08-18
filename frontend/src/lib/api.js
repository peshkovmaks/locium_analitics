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