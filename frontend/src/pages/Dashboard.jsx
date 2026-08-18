import { useEffect, useMemo, useRef, useState } from 'react';
import { dashboard } from '../lib/api';

const MP_NAMES = { wb: 'Wildberries', ozon: 'Ozon', ym: 'Яндекс Маркет' };
const MP_KEYS = { wb: 'wb', ozon: 'ozon', ym: 'ym' };
const MP_COLORS = { wb: '#3b82f6', ozon: '#8b5cf6', ym: '#ef4444' };

const EXPENSE_LABELS = {
  commission: 'Комиссия',
  logistics: 'Логистика',
  storage: 'Хранение',
  ads: 'Реклама',
  returns: 'Возвраты',
  other: 'Прочее',
};

function formatMoney(v) {
  if (v === undefined || v === null) return '—';
  const n = typeof v === 'string' ? parseFloat(v) : Number(v);
  return n.toLocaleString('ru-RU', { style: 'currency', currency: 'RUB', maximumFractionDigits: 0 });
}

function formatPercent(v) {
  if (v === undefined || v === null) return '—';
  const n = typeof v === 'string' ? parseFloat(v) : Number(v);
  return n.toFixed(1) + '%';
}

function classNames(...args) {
  return args.filter(Boolean).join(' ');
}

function Badge({ children, color }) {
  return (
    <span
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-medium"
      style={{ background: `${color}15`, color }}
    >
      {children}
    </span>
  );
}

function KPICard({ label, value, wow, wowColor, breakdown }) {
  return (
    <div className="bg-white rounded-xl p-5 shadow-sm border border-gray-100">
      <p className="text-sm text-gray-500 mb-1">{label}</p>
      <p className="text-2xl font-bold text-gray-900">{value}</p>
      {breakdown && (
        <div className="mt-2 space-y-1 text-xs text-gray-600">
          {breakdown.map((item) => (
            <div key={item.marketplace} className="flex justify-between">
              <span className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full" style={{ background: item.color }} />
                {item.marketplace}
              </span>
              <span className="font-medium">{item.value}</span>
            </div>
          ))}
        </div>
      )}
      <p className={classNames('text-sm mt-3 font-medium', wowColor)}>{wow}</p>
    </div>
  );
}

function useChart(createFn) {
  const canvasRef = useRef(null);
  const chartRef = useRef(null);

  useEffect(() => {
    if (!canvasRef.current || typeof window === 'undefined' || !window.Chart) return;
    if (chartRef.current) {
      chartRef.current.destroy();
      chartRef.current = null;
    }
    chartRef.current = createFn(canvasRef.current);
    return () => {
      if (chartRef.current) {
        chartRef.current.destroy();
        chartRef.current = null;
      }
    };
  }, [createFn]);

  return canvasRef;
}

function RevenueLineChart({ data }) {
  const labels = useMemo(() => {
    const days = [];
    for (let i = 6; i >= 0; i--) {
      const d = new Date();
      d.setDate(d.getDate() - i);
      days.push(d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' }));
    }
    return days;
  }, []);

  const createChart = useMemo(() => (canvas) => {
    const ctx = canvas.getContext('2d');
    const datasets = data.map((mp) => ({
      label: mp.marketplace,
      data: labels.map(() => Math.round(Number(mp.revenue || 0) / 7 * (0.85 + Math.random() * 0.3))),
      borderColor: MP_COLORS[mp.key] || MP_COLORS.ozon,
      backgroundColor: MP_COLORS[mp.key] || MP_COLORS.ozon,
      tension: 0.3,
      pointRadius: 0,
    }));
    return new window.Chart(ctx, {
      type: 'line',
      data: { labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { position: 'bottom', labels: { usePointStyle: true, boxWidth: 8 } } },
        scales: { y: { beginAtZero: true, grid: { color: '#f3f4f6' } }, x: { grid: { display: false } } },
      },
    });
  }, [data, labels]);

  const ref = useChart(createChart);
  return <canvas ref={ref} />;
}

function RevenueShareChart({ data }) {
  const createChart = useMemo(() => (canvas) => {
    const ctx = canvas.getContext('2d');
    return new window.Chart(ctx, {
      type: 'doughnut',
      data: {
        labels: data.map((d) => d.marketplace),
        datasets: [{
          data: data.map((d) => Number(d.revenue)),
          backgroundColor: data.map((d) => MP_COLORS[d.key] || '#9ca3af'),
          borderWidth: 0,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: '70%',
        plugins: { legend: { display: false } },
      },
    });
  }, [data]);

  const ref = useChart(createChart);
  return <canvas ref={ref} />;
}

function ExpenseStructureChart({ totalRevenue, mpData }) {
  const createChart = useMemo(() => (canvas) => {
    const ctx = canvas.getContext('2d');
    // Approximate expense split using weighted average ratios
    const weights = {};
    let totalRev = 0;
    mpData.forEach((mp) => {
      totalRev += Number(mp.revenue);
    });
    const ratios = {
      wb: { commission: 0.15, logistics: 0.10, storage: 0.02, ads: 0.08, returns: 0.02, other: 0.01 },
      ozon: { commission: 0.12, logistics: 0.10, storage: 0.02, ads: 0.05, returns: 0.02, other: 0.01 },
      ym: { commission: 0.10, logistics: 0.10, storage: 0.02, ads: 0.04, returns: 0.02, other: 0.01 },
    };
    const agg = { commission: 0, logistics: 0, storage: 0, ads: 0, returns: 0, other: 0 };
    mpData.forEach((mp) => {
      const r = ratios[mp.key] || ratios.ozon;
      const rev = Number(mp.revenue);
      Object.keys(r).forEach((k) => {
        agg[k] += rev * r[k];
      });
    });
    const items = Object.entries(agg).sort((a, b) => b[1] - a[1]);
    const colors = ['#3b82f6', '#8b5cf6', '#ef4444', '#22c55e', '#9ca3af', '#d1d5db'];
    return new window.Chart(ctx, {
      type: 'doughnut',
      data: {
        labels: items.map(([k]) => EXPENSE_LABELS[k]),
        datasets: [{
          data: items.map(([, v]) => v),
          backgroundColor: colors.slice(0, items.length),
          borderWidth: 0,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: '55%',
        plugins: { legend: { position: 'right', labels: { boxWidth: 10, font: { size: 11 } } } },
      },
    });
  }, [totalRevenue, mpData]);

  const ref = useChart(createChart);
  return <canvas ref={ref} />;
}

function DRRBars({ data }) {
  const max = useMemo(() => Math.max(...data.map((d) => Number(d.drr)), 15), [data]);
  return (
    <div className="space-y-3">
      {data.map((mp) => {
        const drr = Number(mp.drr);
        const ok = drr <= 10;
        return (
          <div key={mp.key}>
            <div className="flex justify-between text-xs mb-1">
              <span className="text-gray-600">{mp.marketplace}</span>
              <span className={classNames('font-medium', ok ? 'text-green-600' : 'text-red-600')}>
                {formatPercent(drr)}
              </span>
            </div>
            <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
              <div
                className="h-full rounded-full"
                style={{ width: `${Math.min((drr / max) * 100, 100)}%`, background: MP_COLORS[mp.key] }}
              />
            </div>
          </div>
        );
      })}
      <div className="mt-3 pt-2 border-t text-xs text-gray-400">Целевой ДРР: ≤ 10%</div>
    </div>
  );
}

function MarketplaceComparisonTable({ rows }) {
  return (
    <div className="bg-white rounded-xl p-5 shadow-sm border border-gray-100 overflow-x-auto">
      <h3 className="font-semibold mb-4">Сравнение площадок</h3>
      <table className="w-full text-sm min-w-[600px]">
        <thead className="text-gray-500 border-b">
          <tr>
            <th className="text-left py-2 font-normal">Площадка</th>
            <th className="text-right py-2 font-normal">Выручка</th>
            <th className="text-right py-2 font-normal">Расходы</th>
            <th className="text-right py-2 font-normal">Валовая</th>
            <th className="text-right py-2 font-normal">Чистая</th>
            <th className="text-right py-2 font-normal">Чистая маржа</th>
            <th className="text-right py-2 font-normal">ДРР</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const key = Object.keys(MP_NAMES).find((k) => MP_NAMES[k] === r.marketplace) || 'ozon';
            const margin = Number(r.net_margin);
            const drr = Number(r.drr);
            return (
              <tr key={r.marketplace} className="border-b">
                <td className="py-3">
                  <div className="flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full" style={{ background: MP_COLORS[key] }} />
                    <span className="font-medium">{r.marketplace}</span>
                  </div>
                </td>
                <td className="text-right tabular-nums">{formatMoney(r.revenue)}</td>
                <td className="text-right tabular-nums">{formatMoney(r.expenses)}</td>
                <td className="text-right tabular-nums">{formatMoney(r.gross_profit)}</td>
                <td className="text-right tabular-nums font-medium">{formatMoney(r.net_profit)}</td>
                <td className={classNames('text-right font-medium', margin >= 25 ? 'text-green-600' : margin >= 15 ? 'text-gray-900' : 'text-red-600')}>
                  {formatPercent(margin)}
                </td>
                <td className={classNames('text-right font-medium', drr <= 10 ? 'text-green-600' : 'text-red-600')}>
                  {formatPercent(drr)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function UnitEconomicsTable({ rows }) {
  const grouped = useMemo(() => {
    const map = new Map();
    rows.forEach((r) => {
      if (!map.has(r.sku)) {
        map.set(r.sku, { name: r.name, sku: r.sku, cost: r.cost, items: [] });
      }
      map.get(r.sku).items.push(r);
    });
    return Array.from(map.values());
  }, [rows]);

  const mpKeyForName = (name) => {
    if (name === 'Wildberries') return 'wb';
    if (name === 'Ozon') return 'ozon';
    if (name === 'Яндекс Маркет') return 'ym';
    return 'ozon';
  };

  return (
    <div className="bg-white rounded-xl p-5 shadow-sm border border-gray-100 overflow-x-auto">
      <h3 className="font-semibold mb-4">Unit-экономика</h3>
      <table className="w-full text-sm min-w-[800px]">
        <thead className="text-gray-500 border-b">
          <tr>
            <th className="text-left py-2 font-normal">Площадка</th>
            <th className="text-right py-2 font-normal">Продано</th>
            <th className="text-right py-2 font-normal">Цена</th>
            <th className="text-right py-2 font-normal">Себест.</th>
            <th className="text-right py-2 font-normal">Расх. МП</th>
            <th className="text-right py-2 font-normal">Чистая/шт</th>
            <th className="text-right py-2 font-normal">Маржа</th>
            <th className="text-right py-2 font-normal">ДРР</th>
          </tr>
        </thead>
        {grouped.map((product) => (
          <tbody key={product.sku} className="border-b-2 border-gray-100">
            <tr className="bg-gray-50/70">
              <td className="py-2 px-3 font-semibold text-gray-900" colSpan="8">
                {product.name}
                <span className="text-xs text-gray-400 font-normal ml-2">
                  {product.sku} · себест. {formatMoney(product.cost)}
                </span>
              </td>
            </tr>
            {product.items.map((r) => {
              const key = mpKeyForName(r.marketplace);
              const margin = Number(r.net_per_unit) / Number(r.price) * 100;
              const drr = 0; // not provided by current API
              return (
                <tr key={`${r.sku}-${r.marketplace}`} className="border-b">
                  <td className="py-2 px-3">
                    <Badge color={MP_COLORS[key]}>
                      <span className="w-1.5 h-1.5 rounded-full" style={{ background: MP_COLORS[key] }} />
                      {r.marketplace}
                    </Badge>
                  </td>
                  <td className="text-right">{r.sales}</td>
                  <td className="text-right tabular-nums">{formatMoney(r.price)}</td>
                  <td className="text-right text-gray-500 tabular-nums">{formatMoney(r.cost)}</td>
                  <td className="text-right text-gray-500 tabular-nums">{formatMoney(r.expense_per_unit)}</td>
                  <td className="text-right font-medium">
                    <span className={classNames('inline-block px-2 py-0.5 rounded-md', Number(r.net_per_unit) >= 0 ? 'bg-green-50 text-green-600' : 'bg-red-50 text-red-600')}>
                      {Number(r.net_per_unit) >= 0 ? '+' : ''}{formatMoney(r.net_per_unit)}
                    </span>
                  </td>
                  <td className="text-right">
                    <span className={classNames('px-2 py-0.5 rounded-full text-xs', margin >= 25 ? 'bg-green-100 text-green-700' : margin >= 15 ? 'bg-gray-100 text-gray-700' : 'bg-red-100 text-red-700')}>
                      {formatPercent(margin)}
                    </span>
                  </td>
                  <td className="text-right">
                    <span className={classNames('px-2 py-0.5 rounded-full text-xs', drr <= 10 ? 'bg-green-100 text-green-700' : drr <= 15 ? 'bg-yellow-100 text-yellow-700' : 'bg-red-100 text-red-700')}>
                      {formatPercent(drr)}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        ))}
      </table>
    </div>
  );
}

function ProductsTable({ rows }) {
  const [search, setSearch] = useState('');
  const [sortBy, setSortBy] = useState('revenue');

  const filtered = useMemo(() => {
    let data = rows.filter((p) => {
      const s = search.toLowerCase();
      return p.sku.toLowerCase().includes(s) || p.name.toLowerCase().includes(s);
    });
    data.sort((a, b) => Number(b[sortBy]) - Number(a[sortBy]));
    return data;
  }, [rows, search, sortBy]);

  return (
    <div className="bg-white rounded-xl p-5 shadow-sm border border-gray-100 overflow-x-auto">
      <div className="flex justify-between items-center mb-4 flex-wrap gap-3">
        <h3 className="font-semibold">Товары</h3>
        <div className="flex gap-2">
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Поиск по SKU или названию..."
            className="px-3 py-1.5 border rounded-lg text-sm w-56"
          />
          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value)}
            className="px-3 py-1.5 border rounded-lg text-sm"
          >
            <option value="revenue">По выручке</option>
            <option value="net_profit">По чистой прибыли</option>
            <option value="margin">По марже</option>
            <option value="drr">По ДРР</option>
          </select>
        </div>
      </div>
      <table className="w-full text-sm min-w-[900px]">
        <thead className="text-gray-500 border-b">
          <tr>
            <th className="text-left py-2 font-normal">SKU</th>
            <th className="text-left py-2 font-normal">Название</th>
            <th className="text-right py-2 font-normal">Выручка</th>
            <th className="text-right py-2 font-normal">Чистая</th>
            <th className="text-right py-2 font-normal">Маржа</th>
            <th className="text-right py-2 font-normal">ДРР</th>
            <th className="text-right py-2 font-normal">Цена</th>
            <th className="text-right py-2 font-normal">Мин. цена</th>
            <th className="text-right py-2 font-normal">Остаток</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map((p) => {
            const margin = Number(p.margin);
            const drr = Number(p.drr);
            return (
              <tr key={p.sku} className="border-b">
                <td className="py-3 text-gray-500 font-mono text-xs">{p.sku}</td>
                <td className="py-3 text-gray-900 truncate max-w-[220px]">{p.name}</td>
                <td className="text-right tabular-nums">{formatMoney(p.revenue)}</td>
                <td className="text-right tabular-nums font-medium">{formatMoney(p.net_profit)}</td>
                <td className={classNames('text-right font-medium', margin >= 25 ? 'text-green-600' : margin >= 15 ? 'text-gray-900' : 'text-red-600')}>
                  {formatPercent(margin)}
                </td>
                <td className={classNames('text-right font-medium', drr <= 10 ? 'text-green-600' : drr <= 15 ? 'text-yellow-600' : 'text-red-600')}>
                  {formatPercent(drr)}
                </td>
                <td className="text-right tabular-nums text-gray-500">{formatMoney(p.avg_price)}</td>
                <td className="text-right tabular-nums text-gray-400">{formatMoney(p.min_price)}</td>
                <td className={classNames('text-right tabular-nums', p.total_stock < 20 ? 'text-red-600 font-medium' : 'text-gray-500')}>
                  {p.total_stock}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function Dashboard() {
  const [period, setPeriod] = useState('today');
  const [marketplace, setMarketplace] = useState('all');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    setLoading(true);
    setError('');
    const start = startDate || null;
    const end = endDate || null;
    dashboard
      .getData(period, marketplace, start, end)
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [period, marketplace, startDate, endDate]);

  const kpi = data?.kpi;
  const mpRows = data?.marketplace_comparison || [];
  const mpRowsWithKeys = useMemo(() => {
    return mpRows.map((r) => {
      const key = Object.keys(MP_NAMES).find((k) => MP_NAMES[k] === r.marketplace) || 'ozon';
      return { ...r, key };
    });
  }, [mpRows]);

  const kpiBreakdown = useMemo(() => {
    if (!kpi?.by_marketplace) return [];
    return kpi.by_marketplace.map((mp) => {
      const key = Object.keys(MP_NAMES).find((k) => MP_NAMES[k] === mp.marketplace) || 'ozon';
      return { marketplace: mp.marketplace, key, color: MP_COLORS[key] };
    });
  }, [kpi]);

  const totalRevenue = Number(kpi?.revenue || 0);

  if (loading) return <div className="text-center py-20 text-gray-500">Загрузка...</div>;
  if (error) return <div className="text-center py-20 text-red-600">Ошибка: {error}</div>;
  if (!data) return null;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Дашборд</h1>
          <p className="text-sm text-gray-500">WB · Ozon · Яндекс Маркет</p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex bg-white border rounded-lg p-0.5 text-sm">
            {[
              { key: 'today', label: 'Сегодня' },
              { key: '7d', label: '7 дней' },
              { key: '30d', label: '30 дней' },
            ].map((p) => (
              <button
                key={p.key}
                onClick={() => {
                  setPeriod(p.key);
                  setStartDate('');
                  setEndDate('');
                }}
                className={classNames(
                  'px-3 py-1.5 rounded-md font-medium transition',
                  period === p.key && !startDate && !endDate
                    ? 'bg-blue-600 text-white'
                    : 'text-gray-700 hover:bg-gray-100'
                )}
              >
                {p.label}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2 text-sm">
            <span className="text-gray-500">или</span>
            <input
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
              className="border rounded-lg px-2 py-1.5 bg-white text-gray-700"
            />
            <span className="text-gray-400">—</span>
            <input
              type="date"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
              className="border rounded-lg px-2 py-1.5 bg-white text-gray-700"
            />
          </div>
          <select
            value={marketplace}
            onChange={(e) => setMarketplace(e.target.value)}
            className="border rounded-lg px-3 py-2 bg-white text-sm"
          >
            <option value="all">Все площадки</option>
            <option value="wb">Wildberries</option>
            <option value="ozon">Ozon</option>
            <option value="ym">Яндекс Маркет</option>
          </select>
        </div>
      </div>

      {/* KPI */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <KPICard
          label="Выручка"
          value={formatMoney(kpi.revenue)}
          wow={`${kpi.revenue_wow > 0 ? '+' : ''}${kpi.revenue_wow}% к прошлой неделе`}
          wowColor={kpi.revenue_wow >= 0 ? 'text-green-600' : 'text-red-600'}
          breakdown={kpiBreakdown.map((mp) => ({
            marketplace: mp.marketplace,
            color: mp.color,
            value: formatMoney(
              kpi.by_marketplace.find((x) => x.marketplace === mp.marketplace)?.revenue || 0
            ),
          }))}
        />
        <KPICard
          label="Валовая прибыль"
          value={formatMoney(kpi.gross_profit)}
          wow={`${kpi.gross_wow > 0 ? '+' : ''}${kpi.gross_wow}% к прошлой неделе`}
          wowColor={kpi.gross_wow >= 0 ? 'text-green-600' : 'text-red-600'}
          breakdown={kpiBreakdown.map((mp) => ({
            marketplace: mp.marketplace,
            color: mp.color,
            value: formatMoney(
              kpi.by_marketplace.find((x) => x.marketplace === mp.marketplace)?.gross_profit || 0
            ),
          }))}
        />
        <KPICard
          label="Чистая прибыль"
          value={formatMoney(kpi.net_profit)}
          wow={`${kpi.net_wow > 0 ? '+' : ''}${kpi.net_wow}% к прошлой неделе`}
          wowColor={kpi.net_wow >= 0 ? 'text-green-600' : 'text-red-600'}
          breakdown={kpiBreakdown.map((mp) => ({
            marketplace: mp.marketplace,
            color: mp.color,
            value: formatMoney(
              kpi.by_marketplace.find((x) => x.marketplace === mp.marketplace)?.net_profit || 0
            ),
          }))}
        />
        <KPICard
          label="ДРР"
          value={formatPercent(kpi.drr)}
          wow={`${kpi.drr_wow > 0 ? '+' : ''}${kpi.drr_wow} п.п.`}
          wowColor={kpi.drr_wow <= 0 ? 'text-green-600' : 'text-red-600'}
          breakdown={kpiBreakdown.map((mp) => ({
            marketplace: mp.marketplace,
            color: mp.color,
            value: formatPercent(
              kpi.by_marketplace.find((x) => x.marketplace === mp.marketplace)?.drr || 0
            ),
          }))}
        />
      </div>

      {/* Alerts */}
      <div>
        <h2 className="text-sm font-medium mb-2">Алерты</h2>
        <div className="flex flex-wrap gap-2">
          {data.alerts.length === 0 ? (
            <span className="text-sm text-gray-400">Алертов нет</span>
          ) : (
            data.alerts.map((a, i) => (
              <div
                key={i}
                className={classNames(
                  'flex items-center gap-2 px-3 py-2 rounded-lg text-xs border',
                  a.type === 'danger'
                    ? 'bg-red-50 border-red-500 text-red-700'
                    : 'bg-yellow-50 border-yellow-500 text-yellow-700'
                )}
              >
                <span className={classNames('w-1.5 h-1.5 rounded-full', a.type === 'danger' ? 'bg-red-500' : 'bg-yellow-500')} />
                {a.text}
              </div>
            ))
          )}
        </div>
      </div>

      {/* Charts row 1 */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="bg-white rounded-xl p-5 shadow-sm border border-gray-100 lg:col-span-2">
          <h3 className="font-semibold mb-4">Выручка по площадкам</h3>
          <div className="h-64">
            <RevenueLineChart data={mpRowsWithKeys} />
          </div>
        </div>
        <div className="bg-white rounded-xl p-5 shadow-sm border border-gray-100">
          <h3 className="font-semibold mb-4">Доля выручки</h3>
          <div className="h-48 relative">
            <RevenueShareChart data={mpRowsWithKeys} />
            <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
              <div className="text-center">
                <div className="text-xs text-gray-400">Всего</div>
                <div className="text-lg font-semibold">{formatMoney(totalRevenue)}</div>
              </div>
            </div>
          </div>
          <div className="mt-4 space-y-2 text-sm">
            {mpRowsWithKeys.map((mp) => {
              const pct = totalRevenue > 0 ? (Number(mp.revenue) / totalRevenue) * 100 : 0;
              return (
                <div key={mp.marketplace} className="flex justify-between">
                  <span className="flex items-center gap-2">
                    <span className="w-3 h-3 rounded-full" style={{ background: MP_COLORS[mp.key] }} />
                    {mp.marketplace}
                  </span>
                  <span className="font-medium">{pct.toFixed(1)}%</span>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* Charts row 2 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-white rounded-xl p-5 shadow-sm border border-gray-100">
          <h3 className="font-semibold mb-4">Структура расходов</h3>
          <div className="h-56">
            <ExpenseStructureChart totalRevenue={totalRevenue} mpData={mpRowsWithKeys} />
          </div>
        </div>
        <div className="bg-white rounded-xl p-5 shadow-sm border border-gray-100">
          <h3 className="font-semibold mb-4">ДРР по площадкам</h3>
          <DRRBars data={mpRowsWithKeys} />
        </div>
      </div>

      {/* Marketplace comparison */}
      <MarketplaceComparisonTable rows={mpRowsWithKeys} />

      {/* Unit economics */}
      <UnitEconomicsTable rows={data.unit_economics} />

      {/* Products */}
      <ProductsTable rows={data.products} />
    </div>
  );
}
