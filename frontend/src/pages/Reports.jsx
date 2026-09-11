import { useMemo, useState } from 'react';
import { reports } from '../lib/api';

const TABS = [
  { key: 'report', label: 'Отчёт PDF' },
  { key: 'abc', label: 'ABC-классификация' },
  { key: 'reconciliation', label: 'Сверка' },
  { key: 'plan-fact', label: 'План-факт' },
];

const ABC_BADGES = {
  A: 'bg-green-100 text-green-700',
  B: 'bg-amber-100 text-amber-700',
  C: 'bg-red-100 text-red-700',
  D: 'bg-slate-200 text-slate-600',
};

const RECONCILIATION_STATUS = {
  preliminary: { className: 'bg-amber-100 text-amber-700', label: 'Предварительные данные' },
  closed: { className: 'bg-green-100 text-green-700', label: 'Период закрыт' },
  has_discrepancies: { className: 'bg-red-100 text-red-700', label: 'Есть расхождения' },
};

const PLAN_FACT_STATUS = {
  on_track: { className: 'bg-green-100 text-green-700', label: 'В норме' },
  at_risk: { className: 'bg-amber-100 text-amber-700', label: 'Зона риска' },
  off_track: { className: 'bg-red-100 text-red-700', label: 'Отклонение' },
};

const TARGET_FIELDS = [
  { key: 'revenue', label: 'Выручка, ₽' },
  { key: 'profit', label: 'Прибыль, ₽' },
  { key: 'margin', label: 'Маржа, %' },
  { key: 'drr', label: 'ДРР, %' },
  { key: 'orders', label: 'Заказы, шт' },
  { key: 'returns', label: 'Возвраты, %' },
  { key: 'stock', label: 'Остатки, шт' },
];

const percent = (value) => value === null || value === undefined ? '—' : `${Number(value).toFixed(1)}%`;
const signedMoney = (value) => value === null || value === undefined ? '—' : money(value);
const signedPercent = (value) => value === null || value === undefined ? '—' : `${value > 0 ? '+' : ''}${Number(value).toFixed(1)}%`;

function MonthPicker({ month, setMonth, onLoad, loading, buttonLabel = 'Загрузить' }) {
  return (
    <div className="flex flex-wrap items-end gap-3 rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <label className="text-sm text-gray-600">Месяц
        <input type="month" value={month} onChange={(event) => setMonth(event.target.value)} className="mt-1 block rounded-lg border-gray-300 text-sm" />
      </label>
      <button type="button" onClick={onLoad} disabled={loading || !month} className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-gray-300">{loading ? 'Загружаем…' : buttonLabel}</button>
    </div>
  );
}

function AbcTab({ month, setMonth }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      setData(await reports.abc(month));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="space-y-5">
      <MonthPicker month={month} setMonth={setMonth} onLoad={load} loading={loading} />
      {error && <p className="text-sm text-red-600">{error}</p>}
      {data && (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
            {['A', 'B', 'C', 'D'].map((letter) => (
              <div key={letter} className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
                <p className="text-sm text-gray-500">Класс {letter}</p>
                <p className="mt-2 text-xl font-bold text-gray-900">{data.summary[letter]} SKU</p>
              </div>
            ))}
            <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
              <p className="text-sm text-gray-500">Доля прибыли класса A</p>
              <p className="mt-2 text-xl font-bold text-gray-900">{percent(data.a_profit_share_percent)}</p>
            </div>
          </div>
          <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <h2 className="font-semibold text-gray-900">Классификация SKU за {data.month}</h2>
            <div className="mt-4 overflow-x-auto">
              <table className="w-full min-w-[900px] text-sm">
                <thead className="border-b text-left text-gray-500">
                  <tr>
                    <th className="py-2">SKU</th>
                    <th className="py-2">Товар</th>
                    <th className="py-2">Класс</th>
                    <th className="py-2 text-right">Выручка</th>
                    <th className="py-2 text-right">Прибыль</th>
                    <th className="py-2 text-right">Маржа</th>
                    <th className="py-2 text-right">ДРР</th>
                    <th className="py-2 text-right">Возвраты</th>
                    <th className="py-2 text-right">Продано</th>
                    <th className="py-2 text-right">Остаток</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((row) => (
                    <tr key={row.sku} className="border-b last:border-0">
                      <td className="py-3 font-medium">{row.sku}</td>
                      <td className="py-3">{row.name}</td>
                      <td className="py-3"><span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${ABC_BADGES[row.class]}`}>{row.class}</span></td>
                      <td className="py-3 text-right">{money(row.revenue)}</td>
                      <td className="py-3 text-right">{money(row.profit)}</td>
                      <td className="py-3 text-right">{percent(row.margin_percent)}</td>
                      <td className="py-3 text-right">{percent(row.drr_percent)}</td>
                      <td className="py-3 text-right">{percent(row.return_rate_percent)}</td>
                      <td className="py-3 text-right">{row.items_sold}</td>
                      <td className="py-3 text-right">{row.stock}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </section>
  );
}

function ReconciliationTab({ month, setMonth }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      setData(await reports.reconciliation(month));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const status = data ? RECONCILIATION_STATUS[data.status] : null;

  return (
    <section className="space-y-5">
      <MonthPicker month={month} setMonth={setMonth} onLoad={load} loading={loading} />
      {error && <p className="text-sm text-red-600">{error}</p>}
      {data && (
        <>
          <div className="flex flex-wrap items-center gap-3 rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
            {status && <span className={`rounded-full px-3 py-1 text-sm font-semibold ${status.className}`}>{status.label}</span>}
            {!data.payouts_available && <span className="text-sm text-gray-500">Выплаты: данные недоступны</span>}
            <span className="text-sm text-gray-500">Допуск расхождения: {percent(data.tolerance_percent)}</span>
          </div>
          <p className="text-sm text-gray-500">{data.note}</p>
          {[{ key: 'total', marketplace: 'Итого' }, ...data.by_marketplace].map((block) => (
            <div key={block.key} className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
              <h2 className="font-semibold text-gray-900">{block.marketplace}</h2>
              <div className="mt-4 overflow-x-auto">
                <table className="w-full min-w-[720px] text-sm">
                  <thead className="border-b text-left text-gray-500">
                    <tr>
                      <th className="py-2">Показатель</th>
                      <th className="py-2 text-right">Продажи</th>
                      <th className="py-2 text-right">Начисления</th>
                      <th className="py-2 text-right">Выплаты</th>
                      <th className="py-2 text-right">Расхождение</th>
                    </tr>
                  </thead>
                  <tbody>
                    {block.rows.map((row) => (
                      <tr key={row.key} className="border-b last:border-0">
                        <td className="py-3 font-medium">{row.label}</td>
                        <td className="py-3 text-right">{money(row.sales)}</td>
                        <td className="py-3 text-right">{money(row.accruals)}</td>
                        <td className="py-3 text-right">{row.payouts === null ? '—' : money(row.payouts)}</td>
                        <td className={`py-3 text-right ${Math.abs(row.discrepancy_percent) > data.tolerance_percent ? 'font-semibold text-red-600' : 'text-gray-600'}`}>
                          {money(row.discrepancy)} ({signedPercent(row.discrepancy_percent)})
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </>
      )}
    </section>
  );
}

function PlanFactTab({ month, setMonth }) {
  const [targets, setTargets] = useState({});
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);

  const load = async () => {
    setLoading(true);
    setError('');
    setSaved(false);
    try {
      const [existing, planFact] = await Promise.all([
        reports.getTarget(month).catch(() => null),
        reports.planFact(month).catch(() => null),
      ]);
      setTargets(existing?.targets || {});
      setData(planFact);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const save = async () => {
    setLoading(true);
    setError('');
    setSaved(false);
    try {
      const cleaned = Object.fromEntries(
        Object.entries(targets).filter(([, value]) => value !== '' && value !== null && value !== undefined)
      );
      await reports.saveTarget(month, { targets: cleaned });
      setData(await reports.planFact(month));
      setSaved(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="space-y-5">
      <MonthPicker month={month} setMonth={setMonth} onLoad={load} loading={loading} buttonLabel="Загрузить цели" />
      <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
        <h2 className="font-semibold text-gray-900">Цели на месяц</h2>
        <p className="mt-1 text-sm text-gray-500">Заполните только нужные показатели — пустые поля не сохраняются.</p>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {TARGET_FIELDS.map((field) => (
            <label key={field.key} className="text-sm text-gray-600">{field.label}
              <input
                type="number"
                value={targets[field.key] ?? ''}
                onChange={(event) => setTargets((current) => ({ ...current, [field.key]: event.target.value }))}
                className="mt-1 block w-full rounded-lg border-gray-300 text-sm"
              />
            </label>
          ))}
        </div>
        <button type="button" onClick={save} disabled={loading || !month} className="mt-4 rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-gray-300">
          {loading ? 'Сохраняем…' : 'Сохранить цели и показать план-факт'}
        </button>
        {saved && <p className="mt-2 text-xs text-green-600">Цели сохранены</p>}
        {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
      </div>
      {data && (
        <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <h2 className="font-semibold text-gray-900">План-факт за {data.month}</h2>
          <p className="mt-1 text-sm text-gray-500">{data.note}</p>
          <div className="mt-4 overflow-x-auto">
            <table className="w-full min-w-[720px] text-sm">
              <thead className="border-b text-left text-gray-500">
                <tr>
                  <th className="py-2">Показатель</th>
                  <th className="py-2 text-right">План</th>
                  <th className="py-2 text-right">Факт</th>
                  <th className="py-2 text-right">Отклонение</th>
                  <th className="py-2 text-right">Отклонение, %</th>
                  <th className="py-2">Статус</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row) => {
                  const status = row.status ? PLAN_FACT_STATUS[row.status] : null;
                  return (
                    <tr key={row.metric} className="border-b last:border-0">
                      <td className="py-3 font-medium">{row.label}</td>
                      <td className="py-3 text-right">{row.plan ?? '—'}</td>
                      <td className="py-3 text-right">{row.fact === null ? '—' : row.metric === 'orders' ? row.fact : money(row.fact)}</td>
                      <td className="py-3 text-right">{signedMoney(row.deviation)}</td>
                      <td className="py-3 text-right">{signedPercent(row.deviation_percent)}</td>
                      <td className="py-3">
                        {status && <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${status.className}`}>{status.label}</span>}
                        {row.note && <span className="ml-2 text-xs text-gray-400">{row.note}</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}


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
  const [tab, setTab] = useState('report');
  const [month, setMonth] = useState(new Date().toISOString().slice(0, 7));

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

      <div className="flex flex-wrap gap-2 border-b border-gray-200">
        {TABS.map((item) => (
          <button
            key={item.key}
            type="button"
            onClick={() => setTab(item.key)}
            className={`border-b-2 px-4 py-2 text-sm font-medium transition ${tab === item.key ? 'border-blue-600 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}
          >
            {item.label}
          </button>
        ))}
      </div>

      {tab !== 'report' && (
        tab === 'abc'
          ? <AbcTab month={month} setMonth={setMonth} />
          : tab === 'reconciliation'
            ? <ReconciliationTab month={month} setMonth={setMonth} />
            : <PlanFactTab month={month} setMonth={setMonth} />
      )}

      {tab === 'report' && (
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
      )}
    </div>
  );
}