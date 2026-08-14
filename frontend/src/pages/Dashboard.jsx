import { useEffect, useState } from 'react';
import { dashboard } from '../lib/api';

const MP_NAMES = { wb: 'Wildberries', ozon: 'Ozon', ym: 'Яндекс Маркет' };

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

export default function Dashboard() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [period, setPeriod] = useState('today');
  const [error, setError] = useState('');

  useEffect(() => {
    setLoading(true);
    dashboard.getData(period)
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [period]);

  if (loading) return <div className="text-center py-20 text-gray-500">Загрузка...</div>;
  if (error) return <div className="text-center py-20 text-red-600">Ошибка: {error}</div>;
  if (!data) return null;

  const { kpi, alerts, marketplace_comparison, unit_economics, products } = data;

  return (
    <div className="space-y-8">
      {/* Period selector */}
      <div className="flex items-center gap-2">
        {[
          { key: 'today', label: 'Сегодня' },
          { key: '7d', label: '7 дней' },
          { key: '30d', label: '30 дней' },
        ].map((p) => (
          <button
            key={p.key}
            onClick={() => setPeriod(p.key)}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition ${
              period === p.key
                ? 'bg-blue-600 text-white'
                : 'bg-white text-gray-700 border border-gray-200 hover:bg-gray-50'
            }`}
          >
            {p.label}
          </button>
        ))}
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {[
          { label: 'Выручка', value: formatMoney(kpi.revenue), wow: `${kpi.revenue_wow > 0 ? '+' : ''}${kpi.revenue_wow}% к прошлой неделе`, positive: kpi.revenue_wow >= 0 },
          { label: 'Валовая прибыль', value: formatMoney(kpi.gross_profit), wow: `${kpi.gross_wow > 0 ? '+' : ''}${kpi.gross_wow}% к прошлой неделе`, positive: kpi.gross_wow >= 0 },
          { label: 'Чистая прибыль', value: formatMoney(kpi.net_profit), wow: `${kpi.net_wow > 0 ? '+' : ''}${kpi.net_wow}% к прошлой неделе`, positive: kpi.net_wow >= 0 },
          { label: 'ДРР', value: formatPercent(kpi.drr), wow: `${kpi.drr_wow > 0 ? '+' : ''}${kpi.drr_wow} п.п.`, positive: kpi.drr_wow <= 0 },
        ].map((kpiItem) => (
          <div key={kpiItem.label} className="bg-white rounded-xl p-6 shadow-sm border border-gray-100">
            <p className="text-sm text-gray-500 mb-1">{kpiItem.label}</p>
            <p className="text-2xl font-bold text-gray-900">{kpiItem.value}</p>
            <p className={`text-sm mt-2 font-medium ${kpiItem.positive ? 'text-green-600' : 'text-red-600'}`}>
              {kpiItem.wow}
            </p>
          </div>
        ))}
      </div>

      {/* Alerts */}
      {alerts.length > 0 && (
        <div className="bg-white rounded-xl p-6 shadow-sm border border-gray-100">
          <h3 className="text-lg font-bold text-gray-900 mb-4">⚠️ Алерты</h3>
          <div className="space-y-2">
            {alerts.map((a, i) => (
              <div key={i} className={`p-3 rounded-lg text-sm ${a.type === 'danger' ? 'bg-red-50 text-red-700' : 'bg-yellow-50 text-yellow-700'}`}>
                {a.text}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Marketplace comparison */}
      <div className="bg-white rounded-xl p-6 shadow-sm border border-gray-100">
        <h3 className="text-lg font-bold text-gray-900 mb-4">Сравнение площадок</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-gray-500 border-b border-gray-100">
                <th className="pb-3 font-medium">Площадка</th>
                <th className="pb-3 font-medium text-right">Выручка</th>
                <th className="pb-3 font-medium text-right">Расходы</th>
                <th className="pb-3 font-medium text-right">Валовая</th>
                <th className="pb-3 font-medium text-right">Чистая</th>
                <th className="pb-3 font-medium text-right">Маржа</th>
                <th className="pb-3 font-medium text-right">ДРР</th>
              </tr>
            </thead>
            <tbody>
              {marketplace_comparison.map((r) => (
                <tr key={r.marketplace} className="border-b border-gray-50">
                  <td className="py-3 font-medium text-gray-900">{r.marketplace}</td>
                  <td className="py-3 text-right">{formatMoney(r.revenue)}</td>
                  <td className="py-3 text-right">{formatMoney(r.expenses)}</td>
                  <td className="py-3 text-right">{formatMoney(r.gross_profit)}</td>
                  <td className="py-3 text-right">{formatMoney(r.net_profit)}</td>
                  <td className={`py-3 text-right font-medium ${r.net_margin >= 25 ? 'text-green-600' : r.net_margin >= 15 ? 'text-gray-900' : 'text-red-600'}`}>
                    {formatPercent(r.net_margin)}
                  </td>
                  <td className="py-3 text-right">{formatPercent(r.drr)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Unit Economics */}
      <div className="bg-white rounded-xl p-6 shadow-sm border border-gray-100">
        <h3 className="text-lg font-bold text-gray-900 mb-4">Unit-экономика</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-gray-500 border-b border-gray-100">
                <th className="pb-3 font-medium">Товар</th>
                <th className="pb-3 font-medium">Площадка</th>
                <th className="pb-3 font-medium text-right">Цена</th>
                <th className="pb-3 font-medium text-right">Себест.</th>
                <th className="pb-3 font-medium text-right">Расх. МП</th>
                <th className="pb-3 font-medium text-right">Чистая/шт</th>
                <th className="pb-3 font-medium text-right">Продано</th>
                <th className="pb-3 font-medium text-right">Всего чистая</th>
              </tr>
            </thead>
            <tbody>
              {unit_economics.map((r) => (
                <tr key={`${r.sku}-${r.marketplace}`} className="border-b border-gray-50">
                  <td className="py-3 font-medium text-gray-900">{r.name}</td>
                  <td className="py-3 text-gray-600">{r.marketplace}</td>
                  <td className="py-3 text-right">{Math.round(r.price)}₽</td>
                  <td className="py-3 text-right">{Math.round(r.cost)}₽</td>
                  <td className="py-3 text-right">{Math.round(r.expense_per_unit)}₽</td>
                  <td className={`py-3 text-right font-medium ${r.net_per_unit > 0 ? 'text-green-600' : 'text-red-600'}`}>
                    {r.net_per_unit > 0 ? '+' : ''}{Math.round(r.net_per_unit)}₽
                  </td>
                  <td className="py-3 text-right">{r.sales} шт</td>
                  <td className="py-3 text-right">{formatMoney(r.total_net)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Products table */}
      <div className="bg-white rounded-xl p-6 shadow-sm border border-gray-100">
        <h3 className="text-lg font-bold text-gray-900 mb-4">Товары</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-gray-500 border-b border-gray-100">
                <th className="pb-3 font-medium">SKU</th>
                <th className="pb-3 font-medium">Название</th>
                <th className="pb-3 font-medium text-right">Выручка</th>
                <th className="pb-3 font-medium text-right">Чистая</th>
                <th className="pb-3 font-medium text-right">Маржа</th>
                <th className="pb-3 font-medium text-right">ДРР</th>
                <th className="pb-3 font-medium text-right">Мин. цена</th>
                <th className="pb-3 font-medium text-right">Остаток</th>
              </tr>
            </thead>
            <tbody>
              {products.map((p) => (
                <tr key={p.sku} className="border-b border-gray-50">
                  <td className="py-3 font-mono text-xs text-gray-600">{p.sku}</td>
                  <td className="py-3 font-medium text-gray-900">{p.name}</td>
                  <td className="py-3 text-right">{formatMoney(p.revenue)}</td>
                  <td className="py-3 text-right">{formatMoney(p.net_profit)}</td>
                  <td className={`py-3 text-right font-medium ${p.margin >= 25 ? 'text-green-600' : p.margin >= 15 ? 'text-gray-900' : 'text-red-600'}`}>
                    {formatPercent(p.margin)}
                  </td>
                  <td className="py-3 text-right">{formatPercent(p.drr)}</td>
                  <td className="py-3 text-right">{p.min_price}₽</td>
                  <td className="py-3 text-right">
                    <span className={p.total_stock < 20 ? 'text-red-600 font-medium' : ''}>
                      {p.total_stock}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}