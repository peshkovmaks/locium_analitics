import { useMemo, useState } from 'react';
import { reports } from '../lib/api';

const MARKETPLACES = [
  { key: 'wb', label: 'Wildberries' },
  { key: 'ozon', label: 'Ozon' },
  { key: 'ym', label: 'Яндекс Маркет' },
];

const METRICS = [
  { key: 'revenue', label: 'Выручка', hint: 'сумма продаж с учётом возвратов' },
  { key: 'actual_revenue', label: 'Фактическая выручка', hint: 'оплата покупателя' },
  { key: 'expenses', label: 'Расходы', hint: 'комиссии, логистика, реклама и прочее' },
  { key: 'gross_profit', label: 'Валовая прибыль', hint: 'выручка минус расходы' },
  { key: 'net_profit', label: 'Чистая прибыль', hint: 'с учётом себестоимости' },
  { key: 'drr', label: 'ДРР', hint: 'доля рекламных расходов' },
  { key: 'orders', label: 'Заказы и товары', hint: 'заказы и количество единиц' },
  { key: 'returns', label: 'Возвраты', hint: 'количество возвратов' },
  { key: 'average_check', label: 'Средний чек', hint: 'фактическая выручка на заказ' },
  { key: 'products', label: 'Топ товаров', hint: '20 товаров по выручке' },
  { key: 'unit_economics', label: 'Юнит-экономика', hint: 'прибыль на единицу и за период' },
  { key: 'daily_trend', label: 'Динамика по дням', hint: 'выручка и заказы по датам' },
];

const money = (value) => Number(value || 0).toLocaleString('ru-RU', {
  style: 'currency', currency: 'RUB', maximumFractionDigits: 0,
});

function formatMetric(key, value) {
  if (key === 'drr') return `${Number(value || 0).toFixed(1)}%`;
  if (['orders', 'items', 'returns'].includes(key)) return Number(value || 0).toLocaleString('ru-RU');
  return money(value);
}

export default function Reports() {
  const [startDate, setStartDate] = useState('2026-08-01');
  const [endDate, setEndDate] = useState('2026-08-31');
  const [marketplaces, setMarketplaces] = useState(['all']);
  const [metrics, setMetrics] = useState(METRICS.map((metric) => metric.key));
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [downloaded, setDownloaded] = useState(false);

  const payload = useMemo(() => ({ start_date: startDate, end_date: endDate, marketplaces, metrics }), [startDate, endDate, marketplaces, metrics]);

  const toggleMetric = (key) => {
    setMetrics((current) => current.includes(key) ? current.filter((item) => item !== key) : [...current, key]);
  };

  const toggleMarketplace = (key) => {
    if (key === 'all') {
      setMarketplaces(['all']);
      return;
    }
    setMarketplaces((current) => {
      const selected = current.includes('all') ? [] : current;
      const next = selected.includes(key) ? selected.filter((item) => item !== key) : [...selected, key];
      return next.length ? next : ['all'];
    });
  };

  const generatePreview = async () => {
    setLoading(true);
    setError('');
    setDownloaded(false);
    try {
      setPreview(await reports.preview(payload));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const downloadPdf = async () => {
    setLoading(true);
    setError('');
    try {
      const blob = await reports.download(payload);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `sales-report-${startDate}-${endDate}.pdf`;
      link.click();
      URL.revokeObjectURL(url);
      setDownloaded(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const chartMax = Math.max(...(preview?.daily || []).map((item) => Number(item.revenue)), 1);

  return (
    <div className="space-y-6">
      <div>
        <p className="text-sm font-semibold uppercase tracking-[0.16em] text-blue-600">Конструктор отчётов</p>
        <h1 className="mt-2 text-3xl font-bold text-gray-900">Продажи в PDF</h1>
        <p className="mt-2 max-w-2xl text-gray-500">Выберите период и показатели. Отчёт будет собран по данным дашборда с разбивкой по маркетплейсам и графиками.</p>
      </div>

      <section className="grid gap-5 lg:grid-cols-[300px_1fr]">
        <aside className="space-y-5 rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <div>
            <h2 className="font-semibold text-gray-900">Период</h2>
            <div className="mt-3 grid gap-3">
              <label className="text-sm text-gray-600">С<input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} className="mt-1 block w-full rounded-lg border-gray-300 text-sm" /></label>
              <label className="text-sm text-gray-600">По<input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} className="mt-1 block w-full rounded-lg border-gray-300 text-sm" /></label>
            </div>
          </div>
          <div>
            <h2 className="font-semibold text-gray-900">Маркетплейсы</h2>
            <div className="mt-3 space-y-2">
              <label className="flex cursor-pointer items-center gap-2 text-sm"><input type="checkbox" checked={marketplaces.includes('all')} onChange={() => toggleMarketplace('all')} />Все площадки</label>
              {MARKETPLACES.map((marketplace) => <label key={marketplace.key} className="flex cursor-pointer items-center gap-2 text-sm"><input type="checkbox" checked={marketplaces.includes('all') || marketplaces.includes(marketplace.key)} onChange={() => toggleMarketplace(marketplace.key)} />{marketplace.label}</label>)}
            </div>
          </div>
          <button type="button" onClick={generatePreview} disabled={loading || !metrics.length} className="w-full rounded-lg bg-blue-600 px-4 py-3 text-sm font-semibold text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-gray-300">{loading ? 'Собираем…' : 'Сформировать предпросмотр'}</button>
          {preview && <button type="button" onClick={downloadPdf} disabled={loading} className="w-full rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm font-semibold text-blue-700 transition hover:bg-blue-100 disabled:opacity-60">Скачать PDF</button>}
          {downloaded && <p className="text-center text-xs text-green-600">PDF скачан</p>}
          {error && <p className="text-sm text-red-600">{error}</p>}
        </aside>

        <div className="space-y-5">
          <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <div className="flex items-start justify-between gap-4"><div><h2 className="font-semibold text-gray-900">Показатели в отчёте</h2><p className="mt-1 text-sm text-gray-500">Отметьте нужные блоки перед генерацией.</p></div><button type="button" onClick={() => setMetrics(metrics.length === METRICS.length ? [] : METRICS.map((metric) => metric.key))} className="text-sm font-medium text-blue-600">{metrics.length === METRICS.length ? 'Снять все' : 'Выбрать все'}</button></div>
            <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{METRICS.map((metric) => <label key={metric.key} className={`cursor-pointer rounded-lg border p-3 transition ${metrics.includes(metric.key) ? 'border-blue-400 bg-blue-50' : 'border-gray-200 hover:border-gray-300'}`}><span className="flex items-start gap-3"><input type="checkbox" checked={metrics.includes(metric.key)} onChange={() => toggleMetric(metric.key)} className="mt-0.5" /><span><span className="block text-sm font-medium text-gray-900">{metric.label}</span><span className="mt-1 block text-xs text-gray-500">{metric.hint}</span></span></span></label>)}</div>
          </section>

          {preview && <section className="space-y-5"><div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{metrics.filter((key) => preview.kpi[key] !== undefined).map((key) => <div key={key} className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm"><p className="text-sm text-gray-500">{METRICS.find((metric) => metric.key === key)?.label}</p><p className="mt-2 text-xl font-bold text-gray-900">{formatMetric(key, preview.kpi[key])}</p></div>)}</div>
            <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm"><h2 className="font-semibold text-gray-900">Разбивка по маркетплейсам</h2><div className="mt-4 overflow-x-auto"><table className="w-full min-w-[680px] text-sm"><thead className="border-b text-left text-gray-500"><tr><th className="py-2">Площадка</th><th className="py-2 text-right">Заказы</th><th className="py-2 text-right">Выручка</th><th className="py-2 text-right">Расходы</th><th className="py-2 text-right">Прибыль</th><th className="py-2 text-right">ДРР</th></tr></thead><tbody>{preview.by_marketplace.map((row) => <tr key={`${row.key}-${row.marketplace}`} className="border-b last:border-0"><td className="py-3 font-medium">{row.marketplace}</td><td className="py-3 text-right">{row.orders.toLocaleString('ru-RU')}</td><td className="py-3 text-right">{money(row.revenue)}</td><td className="py-3 text-right">{money(row.expenses)}</td><td className="py-3 text-right">{money(row.net_profit)}</td><td className="py-3 text-right">{Number(row.drr).toFixed(1)}%</td></tr>)}</tbody></table></div></div>
            {metrics.includes('daily_trend') && <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm"><h2 className="font-semibold text-gray-900">Динамика выручки</h2><div className="mt-5 flex h-48 items-end gap-1 overflow-hidden">{preview.daily.map((item) => <div key={item.date} className="group relative flex-1" title={`${item.date}: ${money(item.revenue)}`}><div className="min-h-[2px] rounded-t bg-blue-500" style={{ height: `${Math.max(Number(item.revenue) / chartMax * 100, 1)}%` }} /></div>)}</div><div className="mt-3 flex justify-between text-xs text-gray-400"><span>{preview.daily[0]?.date}</span><span>{preview.daily[preview.daily.length - 1]?.date}</span></div></div>}
          </section>}
        </div>
      </section>
    </div>
  );
}