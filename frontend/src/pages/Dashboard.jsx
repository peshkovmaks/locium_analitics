import { useEffect, useMemo, useRef, useState } from 'react';
import { dashboard, balances } from '../lib/api';

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

function formatDateTime(v) {
  if (!v) return '—';
  const d = new Date(v);
  if (isNaN(d.getTime())) return '—';
  return d.toLocaleString('ru-RU', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
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

function KPICard({ label, value, wow, wowColor, breakdown, sparklineData, sparklineColor = '#3b82f6' }) {
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
      {sparklineData && sparklineData.length > 0 && (
        <div className="mt-3">
          <MiniSparkline values={sparklineData} color={sparklineColor} />
        </div>
      )}
      <p className={classNames('text-sm mt-3 font-medium', wowColor)}>{wow}</p>
    </div>
  );
}

function OrderStatCard({ label, value, wow, wowColor, subtext, sparklineData, sparklineColor = '#3b82f6' }) {
  return (
    <div className="bg-white rounded-xl p-4 shadow-sm border border-gray-100">
      <p className="text-sm text-gray-500 mb-1">{label}</p>
      <p className="text-xl font-bold text-gray-900">{value}</p>
      {subtext && <p className="text-xs text-gray-400 mt-1">{subtext}</p>}
      {sparklineData && sparklineData.length > 0 && (
        <div className="mt-2">
          <MiniSparkline values={sparklineData} color={sparklineColor} />
        </div>
      )}
      {wow !== undefined && wow !== null && (
        <p className={classNames('text-xs mt-2 font-medium', wowColor)}>{wow}</p>
      )}
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

function MiniSparkline({ values, color = '#3b82f6' }) {
  const createChart = useMemo(() => (canvas) => {
    const ctx = canvas.getContext('2d');
    return new window.Chart(ctx, {
      type: 'line',
      data: {
        labels: values.map((_, i) => i),
        datasets: [{
          data: values,
          borderColor: color,
          backgroundColor: color,
          borderWidth: 2,
          tension: 0.3,
          pointRadius: 0,
          fill: false,
        }],
      },
      options: {
        responsive: false,
        maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
        scales: { x: { display: false }, y: { display: false } },
        layout: { padding: 0 },
      },
    });
  }, [values, color]);

  const ref = useChart(createChart);
  return <canvas ref={ref} width={80} height={30} />;
}

function MiniTrend({ values, color = '#3b82f6', height = 24, barWidth = 3, gap = 1 }) {
  const [tooltip, setTooltip] = useState(null);
  if (!values || values.length === 0) {
    return <span className="text-gray-400">—</span>;
  }
  const max = Math.max(...values, 1);
  const totalWidth = values.length * (barWidth + gap) + gap;
  return (
    <div className="relative inline-flex">
      <svg width={totalWidth} height={height} className="mx-auto">
        {values.map((v, i) => {
          const h = (v / max) * height;
          const x = i * (barWidth + gap) + gap;
          const y = height - h;
          return (
            <rect
              key={i}
              x={x}
              y={y}
              width={barWidth}
              height={h}
              rx={1}
              fill={color}
              onMouseEnter={(e) => setTooltip({ x: e.clientX, y: e.clientY, value: v })}
              onMouseMove={(e) => setTooltip({ x: e.clientX, y: e.clientY, value: v })}
              onMouseLeave={() => setTooltip(null)}
            />
          );
        })}
      </svg>
      {tooltip && (
        <div
          className="fixed px-2 py-1 bg-gray-900 text-white text-xs rounded shadow z-50 pointer-events-none"
          style={{ left: tooltip.x + 8, top: tooltip.y - 28 }}
        >
          {formatMoney(tooltip.value)}
        </div>
      )}
    </div>
  );
}

function RevenueLineChart({ dailyTrend }) {
  const labels = useMemo(() => {
    if (!Array.isArray(dailyTrend) || dailyTrend.length === 0) return [];
    return dailyTrend.map((row) => {
      const d = new Date(row.date);
      return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
    });
  }, [dailyTrend]);

  const createChart = useMemo(() => (canvas) => {
    const ctx = canvas.getContext('2d');
    if (!Array.isArray(dailyTrend) || dailyTrend.length === 0) {
      return new window.Chart(ctx, { type: 'line', data: { labels: [], datasets: [] }, options: { plugins: { legend: { display: false } } } });
    }
    const datasets = [
      {
        label: MP_NAMES.wb,
        data: dailyTrend.map((row) => Number(row.wb_revenue || 0)),
        borderColor: MP_COLORS.wb,
        backgroundColor: MP_COLORS.wb,
        tension: 0.3,
        pointRadius: 0,
      },
      {
        label: MP_NAMES.ozon,
        data: dailyTrend.map((row) => Number(row.ozon_revenue || 0)),
        borderColor: MP_COLORS.ozon,
        backgroundColor: MP_COLORS.ozon,
        tension: 0.3,
        pointRadius: 0,
      },
      {
        label: MP_NAMES.ym,
        data: dailyTrend.map((row) => Number(row.ym_revenue || 0)),
        borderColor: MP_COLORS.ym,
        backgroundColor: MP_COLORS.ym,
        tension: 0.3,
        pointRadius: 0,
      },
    ].filter((ds) => ds.data.some((v) => v > 0));
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
  }, [dailyTrend, labels]);

  const ref = useChart(createChart);
  return <canvas ref={ref} />;
}

function ExpenseStructureChart({ expenseStructure }) {
  const createChart = useMemo(() => (canvas) => {
    const ctx = canvas.getContext('2d');
    const colors = {
      commission: '#3b82f6',
      logistics: '#8b5cf6',
      storage: '#22c55e',
      ads: '#ef4444',
      returns: '#f59e0b',
      other: '#9ca3af',
    };
    const items = Object.entries(expenseStructure || {})
      .filter(([_, v]) => Number(v) > 0)
      .sort((a, b) => Number(b[1]) - Number(a[1]));
    if (items.length === 0) {
      return new window.Chart(ctx, {
        type: 'doughnut',
        data: { labels: [], datasets: [{ data: [], backgroundColor: [], borderWidth: 0 }] },
        options: { plugins: { legend: { display: false } } },
      });
    }
    return new window.Chart(ctx, {
      type: 'doughnut',
      data: {
        labels: items.map(([k]) => EXPENSE_LABELS[k]),
        datasets: [{
          data: items.map(([, v]) => Number(v)),
          backgroundColor: items.map(([k]) => colors[k] || '#9ca3af'),
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
  }, [expenseStructure]);

  const ref = useChart(createChart);
  return <canvas ref={ref} />;
}

function DRRBarChart({ data }) {
  const createChart = useMemo(() => (canvas) => {
    const ctx = canvas.getContext('2d');
    const values = data.map((d) => Number(d.drr));
    const max = Math.max(...values, 15);

    return new window.Chart(ctx, {
      type: 'bar',
      data: {
        labels: data.map((d) => d.marketplace),
        datasets: [{
          data: values,
          backgroundColor: data.map((d) => MP_COLORS[d.key] || '#9ca3af'),
          borderRadius: 4,
          barThickness: 20,
        }],
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (ctx) => `ДРР: ${ctx.raw.toFixed(1)}%`,
            },
          },
          annotation: {
            annotations: {
              targetLine: {
                type: 'line',
                xMin: 10,
                xMax: 10,
                borderColor: '#ef4444',
                borderWidth: 2,
                borderDash: [6, 4],
                label: {
                  content: 'Цель 10%',
                  enabled: true,
                  position: 'start',
                  backgroundColor: 'rgba(239, 68, 68, 0.9)',
                  color: '#fff',
                  font: { size: 10 },
                },
              },
            },
          },
        },
        scales: {
          x: {
            min: 0,
            max: max,
            grid: { color: '#f3f4f6' },
            ticks: { callback: (v) => `${v}%` },
          },
          y: { grid: { display: false } },
        },
      },
      plugins: [{
        id: 'targetLineFallback',
        afterDraw: (chart) => {
          if (window.Chart.annotation) return;
          const { ctx, scales: { x, y } } = chart;
          const xPos = x.getPixelForValue(10);
          ctx.save();
          ctx.beginPath();
          ctx.moveTo(xPos, y.top);
          ctx.lineTo(xPos, y.bottom);
          ctx.strokeStyle = '#ef4444';
          ctx.lineWidth = 2;
          ctx.setLineDash([6, 4]);
          ctx.stroke();
          ctx.restore();
        },
      }],
    });
  }, [data]);

  const ref = useChart(createChart);
  return (
    <div className="h-48">
      <canvas ref={ref} />
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

const MP_KEY_BY_NAME = {
  Wildberries: 'wb',
  Ozon: 'ozon',
  'Яндекс Маркет': 'ym',
};

function UnitEconomicsTable({ rows }) {
  return (
    <div className="bg-white rounded-xl p-5 shadow-sm border border-gray-100 overflow-x-auto">
      <h3 className="font-semibold mb-4">Unit-экономика</h3>
      <table className="w-full text-sm min-w-[800px]">
        <thead className="text-gray-500 border-b">
          <tr>
            <th className="text-left py-2 font-normal">Площадка</th>
            <th className="text-right py-2 font-normal">Продано</th>
            <th className="text-right py-2 font-normal">Средняя цена</th>
            <th className="text-right py-2 font-normal">Себест.</th>
            <th className="text-right py-2 font-normal">Расх. МП</th>
            <th className="text-right py-2 font-normal">Чистая/шт</th>
            <th className="text-right py-2 font-normal">Маржа</th>
            <th className="text-right py-2 font-normal">Тренд</th>
            <th className="text-right py-2 font-normal">ДРР</th>
          </tr>
        </thead>
        {rows.map((product) => (
          <tbody key={product.sku} className="border-b-2 border-gray-100">
            <tr className="bg-gray-50/70">
              <td className="py-2 px-3 font-semibold text-gray-900" colSpan="9">
                {product.name}
                <span className="text-xs text-gray-400 font-normal ml-2">
                  {product.sku} · себест. {formatMoney(product.cost)}
                </span>
              </td>
            </tr>
            {product.rows.map((r) => {
              const key = MP_KEY_BY_NAME[r.marketplace] || 'ozon';
              const margin = Number(r.margin);
              const drr = Number(r.drr);
              return (
                <tr key={`${product.sku}-${r.marketplace}`} className="border-b">
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
                    <MiniSparkline values={r.trend || []} color={MP_COLORS[key]} />
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

function ProductsTable({ rows, unitEconomics }) {
  const [search, setSearch] = useState('');
  const [sortBy, setSortBy] = useState('revenue');
  const [expandedRows, setExpandedRows] = useState(new Set());
  const [zeroOpen, setZeroOpen] = useState(false);

  const unitMap = useMemo(() => {
    const map = {};
    if (!Array.isArray(unitEconomics)) return map;
    unitEconomics.forEach((u) => {
      if (u && u.sku) map[u.sku] = u;
    });
    return map;
  }, [unitEconomics]);

  const filtered = useMemo(() => {
    let data = rows.filter((p) => {
      const s = search.toLowerCase();
      return p.sku.toLowerCase().includes(s) || p.name.toLowerCase().includes(s);
    });
    data.sort((a, b) => Number(b[sortBy]) - Number(a[sortBy]));
    return data;
  }, [rows, search, sortBy]);

  const activeRows = useMemo(() => filtered.filter((p) => Number(p.revenue) > 0), [filtered]);
  const zeroRows = useMemo(() => filtered.filter((p) => Number(p.revenue) === 0), [filtered]);

  const toggleRow = (sku) => {
    setExpandedRows((prev) => {
      const next = new Set(prev);
      if (next.has(sku)) next.delete(sku);
      else next.add(sku);
      return next;
    });
  };

  const renderExpandIcon = (isExpanded) => (
    <svg
      width="12"
      height="12"
      viewBox="0 0 12 12"
      fill="currentColor"
      className={classNames('transition-transform duration-200', isExpanded ? 'rotate-180' : '')}
    >
      <path d="M6 8L1 3h10L6 8z" />
    </svg>
  );

  const TableHead = () => (
    <thead className="text-gray-500 border-b">
      <tr>
        <th className="text-left py-2 font-normal w-8"></th>
        <th className="text-left py-2 font-normal">SKU</th>
        <th className="text-left py-2 font-normal">Название</th>
        <th className="text-right py-2 font-normal">Выручка</th>
        <th className="text-right py-2 font-normal">Чистая</th>
        <th className="text-right py-2 font-normal">Маржа</th>
        <th className="text-right py-2 font-normal">ДРР</th>
        <th className="text-right py-2 font-normal">Средняя цена</th>
        <th className="text-right py-2 font-normal">Мин. цена</th>
      </tr>
    </thead>
  );

  const renderProductRows = (items) =>
    items.flatMap((p) => {
      const margin = Number(p.margin);
      const drr = Number(p.drr);
      const isExpanded = expandedRows.has(p.sku);
      const unit = unitMap[p.sku];
      const hasUnit = unit && Array.isArray(unit.rows) && unit.rows.length > 0;
      const result = [];

      result.push(
        <tr
          key={`${p.sku}-main`}
          className={classNames('border-b', hasUnit ? 'cursor-pointer hover:bg-gray-50' : '')}
          onClick={() => hasUnit && toggleRow(p.sku)}
        >
          <td className="py-3 text-gray-400">
            {hasUnit && renderExpandIcon(isExpanded)}
          </td>
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
        </tr>
      );

      if (isExpanded && hasUnit) {
        result.push(
          <tr key={`${p.sku}-unit`} className="bg-gray-50/70 border-b">
            <td></td>
            <td colSpan="8" className="py-3">
              <div className="text-xs font-medium text-gray-500 mb-2">Юнит-экономика по площадкам</div>
              <table className="w-full text-xs min-w-[700px]">
                <thead className="text-gray-500 border-b">
                  <tr>
                    <th className="text-left py-1.5 font-normal">Площадка</th>
                    <th className="text-right py-1.5 font-normal">Продано</th>
                    <th className="text-right py-1.5 font-normal">Цена</th>
                    <th className="text-right py-1.5 font-normal">Себест.</th>
                    <th className="text-right py-1.5 font-normal">Расх. МП</th>
                    <th className="text-right py-1.5 font-normal">Чистая/шт</th>
                    <th className="text-right py-1.5 font-normal">Маржа</th>
                    <th className="text-center py-1.5 font-normal w-24">Тренд выручки</th>
                    <th className="text-right py-1.5 font-normal">ДРР</th>
                  </tr>
                </thead>
                <tbody>
                  {unit.rows.map((r) => {
                    const key = MP_KEY_BY_NAME[r.marketplace] || 'ozon';
                    const margin = Number(r.margin);
                    const drr = Number(r.drr);
                    return (
                      <tr key={r.marketplace} className="border-b border-gray-100">
                        <td className="py-2">
                          <Badge color={MP_COLORS[key]}>
                            <span className="w-1.5 h-1.5 rounded-full" style={{ background: MP_COLORS[key] }} />
                            {r.marketplace}
                          </Badge>
                        </td>
                        <td className="text-right tabular-nums">{r.sales}</td>
                        <td className="text-right tabular-nums">{formatMoney(r.price)}</td>
                        <td className="text-right text-gray-500 tabular-nums">{formatMoney(r.cost)}</td>
                        <td className="text-right text-gray-500 tabular-nums">{formatMoney(r.expense_per_unit)}</td>
                        <td className="text-right">
                          <span className={classNames('inline-block px-2 py-0.5 rounded-md font-medium', Number(r.net_per_unit) >= 0 ? 'bg-green-50 text-green-600' : 'bg-red-50 text-red-600')}>
                            {Number(r.net_per_unit) >= 0 ? '+' : ''}{formatMoney(r.net_per_unit)}
                          </span>
                        </td>
                        <td className={classNames('text-right font-medium', margin >= 25 ? 'text-green-600' : margin >= 15 ? 'text-gray-900' : 'text-red-600')}>
                          {formatPercent(margin)}
                        </td>
                        <td className="py-2 text-center w-24">
                          <MiniTrend values={r.trend} color={MP_COLORS[key]} />
                        </td>
                        <td className={classNames('text-right font-medium', drr <= 10 ? 'text-green-600' : drr <= 15 ? 'text-yellow-600' : 'text-red-600')}>
                          {formatPercent(drr)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </td>
          </tr>
        );
      }

      return result;
    });

  return (
    <div className="bg-white rounded-xl p-5 shadow-sm border border-gray-100 overflow-x-auto">
      <div className="flex justify-between items-center mb-4 flex-wrap gap-3">
        <h3 className="font-semibold">Товары</h3>
        <div className="text-sm text-gray-500">Кликните по строке, чтобы увидеть юнит-экономику</div>
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
        <TableHead />
        <tbody>{renderProductRows(activeRows)}</tbody>
      </table>

      {zeroRows.length > 0 && (
        <div className="mt-4 border-t border-gray-100 pt-4">
          <button
            onClick={() => setZeroOpen(!zeroOpen)}
            className="flex items-center gap-2 text-sm text-blue-600 hover:text-blue-800 font-medium"
          >
            <span>Товары без продаж ({zeroRows.length})</span>
            <span>{zeroOpen ? '▲' : '▼'}</span>
          </button>
          {zeroOpen && (
            <table className="w-full text-sm min-w-[900px] mt-3">
              <TableHead />
              <tbody>{renderProductRows(zeroRows)}</tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}


function BalancesSection({ items, loading, error }) {
  if (loading) {
    return (
      <div className="bg-white rounded-xl p-5 shadow-sm border border-gray-100">
        <h3 className="font-semibold mb-4">Деньги на счету</h3>
        <div className="text-center py-8 text-gray-500">Загрузка балансов...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-white rounded-xl p-5 shadow-sm border border-gray-100">
        <h3 className="font-semibold mb-4">Деньги на счету</h3>
        <div className="text-center py-8 text-red-600">Ошибка загрузки балансов: {error}</div>
      </div>
    );
  }

  if (!items || items.length === 0) {
    return (
      <div className="bg-white rounded-xl p-5 shadow-sm border border-gray-100">
        <h3 className="font-semibold mb-4">Деньги на счету</h3>
        <div className="text-center py-8 text-gray-400">Нет данных о балансах</div>
      </div>
    );
  }

  const totalBalance = items.reduce((sum, item) => {
    const isSupported = item.balance !== null && item.balance !== undefined && item.balance !== 'not_supported';
    return isSupported ? sum + Number(item.balance) : sum;
  }, 0);

  return (
    <div className="bg-white rounded-xl p-5 shadow-sm border border-gray-100 overflow-x-auto">
      <h3 className="font-semibold mb-4">Деньги на счету</h3>
      <table className="w-full text-sm min-w-[400px]">
        <thead className="text-gray-500 border-b">
          <tr>
            <th className="text-left py-2 font-normal">Маркетплейс</th>
            <th className="text-left py-2 font-normal">Время обновления</th>
            <th className="text-right py-2 font-normal">Баланс</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const key = item.marketplace;
            const isSupported = item.balance !== null && item.balance !== undefined && item.balance !== 'not_supported';
            return (
              <tr key={item.shop_id} className="border-b">
                <td className="py-3">
                  <div className="flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full" style={{ background: MP_COLORS[key] || '#9ca3af' }} />
                    <span className="font-medium">{MP_NAMES[key] || item.marketplace}</span>
                  </div>
                </td>
                <td className="py-3 text-gray-500">{formatDateTime(item.updated_at)}</td>
                <td className="text-right tabular-nums font-medium">
                  {isSupported ? formatMoney(item.balance) : <span className="text-gray-400">не поддерживается</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
        <tfoot className="border-t-2 border-gray-100">
          <tr>
            <td className="py-3 font-semibold text-gray-900">Итого денег на счетах</td>
            <td />
            <td className="text-right tabular-nums font-bold text-gray-900">{formatMoney(totalBalance)}</td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

export default function Dashboard() {
  const [period, setPeriod] = useState('today');
  const [marketplace, setMarketplace] = useState('all');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [dateRange, setDateRange] = useState({ start: '', end: '' });
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [alertsOpen, setAlertsOpen] = useState(true);

  const [balanceItems, setBalanceItems] = useState([]);
  const [balancesLoading, setBalancesLoading] = useState(true);
  const [balancesError, setBalancesError] = useState('');

  useEffect(() => {
    setBalancesLoading(true);
    setBalancesError('');
    balances
      .list()
      .then(setBalanceItems)
      .catch((e) => setBalancesError(e.message))
      .finally(() => setBalancesLoading(false));
  }, []);

  const loadData = () => {
    setLoading(true);
    setError('');
    const start = dateRange.start || null;
    const end = dateRange.end || null;
    dashboard
      .getData(period, marketplace, start, end)
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadData();
  }, [period, marketplace, dateRange]);

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
                  setDateRange({ start: '', end: '' });
                }}
                className={classNames(
                  'px-3 py-1.5 rounded-md font-medium transition',
                  period === p.key && !dateRange.start && !dateRange.end
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
            <button
              onClick={() => setDateRange({ start: startDate, end: endDate })}
              disabled={!startDate || !endDate}
              className="px-3 py-1.5 bg-blue-600 text-white rounded-lg font-medium disabled:opacity-50 disabled:cursor-not-allowed hover:bg-blue-700 transition"
            >
              Применить
            </button>
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
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4">
        <KPICard
          label="Выручка"
          value={formatMoney(kpi.revenue)}
          wow={`${kpi.revenue_wow > 0 ? '+' : ''}${kpi.revenue_wow}% к прошлому периоду`}
          wowColor={kpi.revenue_wow >= 0 ? 'text-green-600' : 'text-red-600'}
          breakdown={kpiBreakdown.map((mp) => ({
            marketplace: mp.marketplace,
            color: mp.color,
            value: formatMoney(
              kpi.by_marketplace.find((x) => x.marketplace === mp.marketplace)?.revenue || 0
            ),
          }))}
          sparklineData={kpi.revenue_trend}
          sparklineColor="#3b82f6"
        />
        <KPICard
          label="Фактическая выручка"
          value={formatMoney(kpi.actual_revenue)}
          wow={`${kpi.revenue_wow > 0 ? '+' : ''}${kpi.revenue_wow}% к прошлому периоду`}
          wowColor={kpi.revenue_wow >= 0 ? 'text-green-600' : 'text-red-600'}
          breakdown={kpiBreakdown.map((mp) => ({
            marketplace: mp.marketplace,
            color: mp.color,
            value: formatMoney(
              kpi.by_marketplace.find((x) => x.marketplace === mp.marketplace)?.actual_revenue || 0
            ),
          }))}
          sparklineData={kpi.actual_revenue_trend || kpi.revenue_trend}
          sparklineColor="#0ea5e9"
        />
        <KPICard
          label="Валовая прибыль"
          value={formatMoney(kpi.gross_profit)}
          wow={`${kpi.gross_wow > 0 ? '+' : ''}${kpi.gross_wow}% к прошлому периоду`}
          wowColor={kpi.gross_wow >= 0 ? 'text-green-600' : 'text-red-600'}
          breakdown={kpiBreakdown.map((mp) => ({
            marketplace: mp.marketplace,
            color: mp.color,
            value: formatMoney(
              kpi.by_marketplace.find((x) => x.marketplace === mp.marketplace)?.gross_profit || 0
            ),
          }))}
          sparklineData={kpi.gross_trend}
          sparklineColor="#22c55e"
        />
        <KPICard
          label="Чистая прибыль"
          value={formatMoney(kpi.net_profit)}
          wow={`${kpi.net_wow > 0 ? '+' : ''}${kpi.net_wow}% к прошлому периоду`}
          wowColor={kpi.net_wow >= 0 ? 'text-green-600' : 'text-red-600'}
          breakdown={kpiBreakdown.map((mp) => ({
            marketplace: mp.marketplace,
            color: mp.color,
            value: formatMoney(
              kpi.by_marketplace.find((x) => x.marketplace === mp.marketplace)?.net_profit || 0
            ),
          }))}
          sparklineData={kpi.net_trend}
          sparklineColor="#16a34a"
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
          sparklineData={kpi.drr_trend}
          sparklineColor="#ef4444"
        />
      </div>

      {/* Order stats */}
      {data.order_stats && (
        <div>
          <h2 className="text-sm font-medium mb-2">По заказам</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-6 gap-4">
            <OrderStatCard
              label="Заказов"
              value={data.order_stats.orders_count}
              wow={`${data.order_stats.orders_count_wow >= 0 ? '+' : ''}${data.order_stats.orders_count_wow}% к прошлому периоду`}
              wowColor={data.order_stats.orders_count_wow >= 0 ? 'text-green-600' : 'text-red-600'}
              sparklineData={data.order_stats.orders_count_trend}
              sparklineColor="#3b82f6"
            />
            <OrderStatCard
              label="Средний чек"
              value={formatMoney(data.order_stats.average_check)}
              wow={`${data.order_stats.average_check_wow >= 0 ? '+' : ''}${data.order_stats.average_check_wow}% к прошлому периоду`}
              wowColor={data.order_stats.average_check_wow >= 0 ? 'text-green-600' : 'text-red-600'}
              sparklineData={data.order_stats.average_check_trend}
              sparklineColor="#8b5cf6"
            />
            <OrderStatCard
              label="Прибыль на заказ"
              value={formatMoney(data.order_stats.average_profit_per_order)}
              wow={`${data.order_stats.average_profit_per_order_wow >= 0 ? '+' : ''}${data.order_stats.average_profit_per_order_wow}% к прошлому периоду`}
              wowColor={data.order_stats.average_profit_per_order_wow >= 0 ? 'text-green-600' : 'text-red-600'}
              sparklineData={data.order_stats.average_profit_per_order_trend}
              sparklineColor="#22c55e"
            />
            <OrderStatCard
              label="Прибыль на товар"
              value={formatMoney(data.order_stats.profit_per_item)}
              wow={`${data.order_stats.profit_per_item_wow >= 0 ? '+' : ''}${data.order_stats.profit_per_item_wow}% к прошлому периоду`}
              wowColor={data.order_stats.profit_per_item_wow >= 0 ? 'text-green-600' : 'text-red-600'}
              sparklineData={data.order_stats.profit_per_item_trend}
              sparklineColor="#10b981"
            />
            <OrderStatCard
              label="Возвратов"
              value={data.order_stats.returns_count}
              subtext={`${Number(data.order_stats.return_rate).toFixed(1)}% от всех заказов`}
              wow={`${data.order_stats.returns_count_wow >= 0 ? '+' : ''}${data.order_stats.returns_count_wow}% к прошлому периоду`}
              wowColor={data.order_stats.returns_count_wow <= 0 ? 'text-green-600' : 'text-red-600'}
              sparklineData={data.order_stats.returns_count_trend}
              sparklineColor="#ef4444"
            />
            <OrderStatCard
              label="Товаров в заказе"
              value={Number(data.order_stats.avg_items_per_order).toFixed(1)}
              wow={`${data.order_stats.avg_items_per_order_wow >= 0 ? '+' : ''}${data.order_stats.avg_items_per_order_wow}% к прошлому периоду`}
              wowColor={data.order_stats.avg_items_per_order_wow >= 0 ? 'text-green-600' : 'text-red-600'}
              sparklineData={data.order_stats.avg_items_per_order_trend}
              sparklineColor="#f59e0b"
            />
          </div>
        </div>
      )}

      {/* Alerts */}
      <div className="bg-white rounded-xl p-5 shadow-sm border border-gray-100">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-medium">Алерты</h2>
            {data.alerts.length > 0 && (
              <span className="inline-flex items-center justify-center px-2 py-0.5 text-xs font-medium bg-gray-100 text-gray-600 rounded-full min-w-[1.5rem]">
                {data.alerts.length}
              </span>
            )}
          </div>
          <button
            onClick={() => setAlertsOpen(!alertsOpen)}
            className="text-sm text-blue-600 hover:text-blue-800 flex items-center gap-1"
          >
            {alertsOpen ? 'Свернуть' : 'Развернуть'}
            <span>{alertsOpen ? '▲' : '▼'}</span>
          </button>
        </div>
        {alertsOpen && (
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
        )}
      </div>

      {/* Balances */}
      <BalancesSection items={balanceItems} loading={balancesLoading} error={balancesError} />

      {/* Charts row 1 */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="bg-white rounded-xl p-5 shadow-sm border border-gray-100 lg:col-span-2">
          <h3 className="font-semibold mb-4">Выручка по площадкам</h3>
          <div className="h-64">
            <RevenueLineChart dailyTrend={data.daily_trend} />
          </div>
        </div>
        <div className="bg-white rounded-xl p-5 shadow-sm border border-gray-100">
          <h3 className="font-semibold mb-4">% расходов</h3>
          <div className="space-y-4">
            {mpRowsWithKeys.map((mp) => {
              const rev = Number(mp.revenue || 0);
              const exp = Number(mp.expenses || 0);
              const pct = rev > 0 ? (exp / rev) * 100 : 0;
              return (
                <div key={mp.marketplace}>
                  <div className="flex items-center justify-between text-sm mb-1.5">
                    <span className="flex items-center gap-2">
                      <span className="w-2 h-2 rounded-full" style={{ background: MP_COLORS[mp.key] }} />
                      {mp.marketplace}
                    </span>
                    <span className="font-semibold">{pct.toFixed(1)}%</span>
                  </div>
                  <div className="h-1.5 w-full bg-gray-100 rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full"
                      style={{ width: `${Math.min(pct, 100)}%`, background: MP_COLORS[mp.key] }}
                    />
                  </div>
                  <div className="mt-1 text-xs text-gray-500 text-right">{formatMoney(exp)}</div>
                </div>
              );
            })}
          </div>
          <div className="mt-5 pt-4 border-t border-gray-100">
            <div className="flex items-center justify-between">
              <span className="text-sm text-gray-500">Всего расходов</span>
              <span className="font-semibold">{formatMoney(mpRowsWithKeys.reduce((sum, mp) => sum + Number(mp.expenses || 0), 0))}</span>
            </div>
          </div>
        </div>
      </div>

      {/* Charts row 2 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-white rounded-xl p-5 shadow-sm border border-gray-100">
          <h3 className="font-semibold mb-4">Структура расходов</h3>
          <div className="h-56">
            <ExpenseStructureChart expenseStructure={data.expense_structure} />
          </div>
        </div>
        <div className="bg-white rounded-xl p-5 shadow-sm border border-gray-100">
          <h3 className="font-semibold mb-4">ДРР по площадкам</h3>
          <DRRBarChart data={mpRowsWithKeys} />
        </div>
      </div>

      {/* Marketplace comparison */}
      <MarketplaceComparisonTable rows={mpRowsWithKeys} />

      {/* Products with expandable unit economics */}
      <ProductsTable rows={data.products} unitEconomics={data.unit_economics} />
    </div>
  );
}
