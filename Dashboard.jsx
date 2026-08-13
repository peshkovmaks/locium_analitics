// Dashboard.jsx — React-компонент дашборда
// Stack: React 19 + TypeScript + Tailwind CSS
// Использует Recharts для графиков (замена SVG)

import React, { useState, useMemo } from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, PieChart, Pie, Cell } from 'recharts';

// --- Типы ---
interface Product {
  sku: string;
  name: string;
  cost: number;
  minPrice: number;
  wb: { price: number; stock: number; sales: number };
  ozon: { price: number; stock: number; sales: number };
  ym: { price: number; stock: number; sales: number };
}

interface ExpenseRatios {
  commission: number;
  logistics: number;
  storage: number;
  ads: number;
  returns: number;
  other: number;
}

type Marketplace = 'wb' | 'ozon' | 'ym';
type Period = 'today' | '7d' | '30d';

// --- Константы ---
const MP_NAMES: Record<Marketplace, string> = {
  wb: 'Wildberries',
  ozon: 'Ozon',
  ym: 'Яндекс Маркет',
};

const MP_COLORS: Record<Marketplace, string> = {
  wb: '#3b82f6',
  ozon: '#8b5cf6',
  ym: '#ef4444',
};

const EXPENSE_RATIOS: Record<Marketplace, ExpenseRatios> = {
  wb: { commission: 0.15, logistics: 0.10, storage: 0.02, ads: 0.08, returns: 0.02, other: 0.01 },
  ozon: { commission: 0.12, logistics: 0.10, storage: 0.02, ads: 0.05, returns: 0.02, other: 0.01 },
  ym: { commission: 0.10, logistics: 0.10, storage: 0.02, ads: 0.04, returns: 0.02, other: 0.01 },
};

const EXPENSE_LABELS: Record<string, string> = {
  commission: 'Комиссия',
  logistics: 'Логистика',
  storage: 'Хранение',
  ads: 'Реклама',
  returns: 'Возвраты',
  other: 'Прочее',
};

const EXPENSE_COLORS = ['#3b82f6', '#8b5cf6', '#ef4444', '#22c55e', '#9ca3af', '#d1d5db'];

// --- Mock-данные (20 товаров) ---
const PRODUCTS: Product[] = [
  { sku: 'SKU-001', name: 'Кружка керамическая "Лес"', cost: 120, minPrice: 450, wb: { price: 520, stock: 45, sales: 12 }, ozon: { price: 490, stock: 30, sales: 8 }, ym: { price: 510, stock: 15, sales: 3 } },
  { sku: 'SKU-002', name: 'Термос 500 мл стальной', cost: 350, minPrice: 1200, wb: { price: 1350, stock: 22, sales: 7 }, ozon: { price: 1290, stock: 18, sales: 5 }, ym: { price: 1320, stock: 8, sales: 2 } },
  { sku: 'SKU-003', name: 'Набор кухонных полотенец 3 шт', cost: 180, minPrice: 650, wb: { price: 720, stock: 60, sales: 25 }, ozon: { price: 690, stock: 40, sales: 15 }, ym: { price: 710, stock: 20, sales: 6 } },
  { sku: 'SKU-004', name: 'Подставка под горячее бамбук', cost: 90, minPrice: 280, wb: { price: 320, stock: 80, sales: 18 }, ozon: { price: 300, stock: 55, sales: 12 }, ym: { price: 310, stock: 25, sales: 4 } },
  { sku: 'SKU-005', name: 'Кофейная пара фарфор', cost: 220, minPrice: 800, wb: { price: 890, stock: 35, sales: 9 }, ozon: { price: 850, stock: 20, sales: 6 }, ym: { price: 870, stock: 12, sales: 2 } },
  { sku: 'SKU-006', name: 'Салфетница металлическая', cost: 150, minPrice: 550, wb: { price: 600, stock: 50, sales: 14 }, ozon: { price: 580, stock: 35, sales: 9 }, ym: { price: 590, stock: 18, sales: 3 } },
  { sku: 'SKU-007', name: 'Доска разделочная дерево', cost: 280, minPrice: 950, wb: { price: 1050, stock: 28, sales: 8 }, ozon: { price: 990, stock: 22, sales: 5 }, ym: { price: 1020, stock: 10, sales: 2 } },
  { sku: 'SKU-008', name: 'Набор ложек чайных 6 шт', cost: 130, minPrice: 480, wb: { price: 540, stock: 70, sales: 20 }, ozon: { price: 520, stock: 45, sales: 13 }, ym: { price: 530, stock: 22, sales: 5 } },
  { sku: 'SKU-009', name: 'Поднос сервировочный', cost: 200, minPrice: 720, wb: { price: 800, stock: 40, sales: 10 }, ozon: { price: 780, stock: 28, sales: 7 }, ym: { price: 790, stock: 14, sales: 2 } },
  { sku: 'SKU-010', name: 'Банка для сыпучих 1.5л', cost: 160, minPrice: 580, wb: { price: 650, stock: 55, sales: 16 }, ozon: { price: 620, stock: 38, sales: 10 }, ym: { price: 640, stock: 18, sales: 4 } },
  { sku: 'SKU-011', name: 'Сито металлическое 20 см', cost: 110, minPrice: 380, wb: { price: 430, stock: 65, sales: 19 }, ozon: { price: 410, stock: 42, sales: 11 }, ym: { price: 420, stock: 20, sales: 4 } },
  { sku: 'SKU-012', name: 'Ковш с антипригарным покрытием', cost: 320, minPrice: 1100, wb: { price: 1250, stock: 25, sales: 7 }, ozon: { price: 1180, stock: 18, sales: 4 }, ym: { price: 1220, stock: 9, sales: 2 } },
  { sku: 'SKU-013', name: 'Набор контейнеров для еды', cost: 190, minPrice: 680, wb: { price: 750, stock: 48, sales: 13 }, ozon: { price: 720, stock: 32, sales: 8 }, ym: { price: 740, stock: 15, sales: 3 } },
  { sku: 'SKU-014', name: 'Подставка для ножей магнитная', cost: 450, minPrice: 1600, wb: { price: 1800, stock: 18, sales: 5 }, ozon: { price: 1700, stock: 14, sales: 3 }, ym: { price: 1750, stock: 7, sales: 1 } },
  { sku: 'SKU-015', name: 'Скалка деревянная', cost: 85, minPrice: 290, wb: { price: 340, stock: 90, sales: 22 }, ozon: { price: 320, stock: 60, sales: 14 }, ym: { price: 330, stock: 28, sales: 5 } },
  { sku: 'SKU-016', name: 'Форма для выпечки силикон', cost: 140, minPrice: 520, wb: { price: 590, stock: 52, sales: 15 }, ozon: { price: 560, stock: 35, sales: 9 }, ym: { price: 580, stock: 17, sales: 3 } },
  { sku: 'SKU-017', name: 'Набор мерных ложек', cost: 95, minPrice: 340, wb: { price: 390, stock: 75, sales: 21 }, ozon: { price: 370, stock: 48, sales: 13 }, ym: { price: 380, stock: 22, sales: 5 } },
  { sku: 'SKU-018', name: 'Дуршлаг нержавейка 24 см', cost: 260, minPrice: 880, wb: { price: 980, stock: 32, sales: 9 }, ozon: { price: 940, stock: 24, sales: 6 }, ym: { price: 960, stock: 11, sales: 2 } },
  { sku: 'SKU-019', name: 'Терка четырёхгранная', cost: 170, minPrice: 600, wb: { price: 680, stock: 44, sales: 12 }, ozon: { price: 650, stock: 30, sales: 8 }, ym: { price: 670, stock: 14, sales: 3 } },
  { sku: 'SKU-020', name: 'Кухонный таймер механический', cost: 75, minPrice: 250, wb: { price: 290, stock: 100, sales: 28 }, ozon: { price: 280, stock: 70, sales: 18 }, ym: { price: 285, stock: 32, sales: 7 } },
];

// --- Утилиты ---
const formatMoney = (n: number) => '₽ ' + Math.round(n).toLocaleString('ru-RU');
const formatPercent = (n: number) => n.toFixed(1) + '%';

// --- Компонент ---
export default function Dashboard() {
  const [period, setPeriod] = useState<Period>('today');
  const [mpFilter, setMpFilter] = useState<'all' | Marketplace>('all');
  const [search, setSearch] = useState('');
  const [sortBy, setSortBy] = useState<'revenue' | 'net' | 'margin' | 'drr'>('revenue');

  const mps: Marketplace[] = mpFilter === 'all' ? ['wb', 'ozon', 'ym'] : [mpFilter];

  // Расчёт метрик
  const metrics = useMemo(() => {
    let totalRevenue = 0, totalExpenses = 0, totalAds = 0;
    const mpData = { wb: 0, ozon: 0, ym: 0 };
    const mpExpenses = { wb: 0, ozon: 0, ym: 0 };

    PRODUCTS.forEach(p => {
      mps.forEach(mp => {
        const rev = p[mp].price * p[mp].sales;
        const r = EXPENSE_RATIOS[mp];
        const exp = rev * (r.commission + r.logistics + r.storage + r.ads + r.returns + r.other);
        totalRevenue += rev;
        totalExpenses += exp;
        totalAds += rev * r.ads;
        mpData[mp] += rev;
        mpExpenses[mp] += exp;
      });
    });

    const totalGross = totalRevenue - totalExpenses;
    const totalCost = PRODUCTS.reduce((s, p) => s + p.cost * mps.reduce((u, mp) => u + p[mp].sales, 0), 0);
    const totalNet = totalGross - totalCost;

    return { totalRevenue, totalExpenses, totalGross, totalNet, totalAds, mpData, mpExpenses };
  }, [mps]);

  // Алерты
  const alerts = useMemo(() => {
    const list: { type: 'danger' | 'warning'; text: string }[] = [];
    PRODUCTS.forEach(p => {
      mps.forEach(mp => {
        if (p[mp].price < p.minPrice) list.push({ type: 'danger', text: `${p.sku} на ${MP_NAMES[mp]}: цена ${p[mp].price}₽ < мин. ${p.minPrice}₽` });
        if (p[mp].stock < 10) list.push({ type: 'warning', text: `${p.sku} на ${MP_NAMES[mp]}: остаток ${p[mp].stock} шт` });
      });
    });
    return list.slice(0, 6);
  }, [mps]);

  // Данные для графика (mock trend)
  const chartData = useMemo(() => {
    const days = ['7 авг','8 авг','9 авг','10 авг','11 авг','12 авг','13 авг'];
    return days.map((day, i) => {
      const point: any = { day };
      mps.forEach(mp => {
        const base = metrics.mpData[mp] / 7;
        point[mp] = Math.round(base * (0.85 + Math.sin(i + mp.length) * 0.15));
      });
      return point;
    });
  }, [mps, metrics]);

  // Данные для donut "доля выручки"
  const shareData = mps.map(mp => ({ name: MP_NAMES[mp], value: metrics.mpData[mp], color: MP_COLORS[mp] }));

  // Данные для donut "расходы"
  const expenseData = useMemo(() => {
    const agg: Record<string, number> = {};
    PRODUCTS.forEach(p => {
      mps.forEach(mp => {
        const rev = p[mp].price * p[mp].sales;
        Object.entries(EXPENSE_RATIOS[mp]).forEach(([k, v]) => {
          agg[k] = (agg[k] || 0) + rev * v;
        });
      });
    });
    return Object.entries(agg)
      .sort((a, b) => b[1] - a[1])
      .map(([key, value], i) => ({ name: EXPENSE_LABELS[key], value, color: EXPENSE_COLORS[i] }));
  }, [mps]);

  // Юнит-экономика
  const unitRows = useMemo(() => {
    const rows: any[] = [];
    PRODUCTS.forEach(p => {
      mps.forEach(mp => {
        const x = p[mp];
        if (x.sales === 0) return;
        const r = EXPENSE_RATIOS[mp];
        const expPerUnit = x.price * (r.commission + r.logistics + r.storage + r.ads + r.returns + r.other);
        const netPerUnit = x.price - p.cost - expPerUnit;
        rows.push({
          name: p.name, sku: p.sku, mp,
          price: x.price, cost: p.cost, expensePerUnit: expPerUnit,
          netPerUnit, sales: x.sales, totalNet: netPerUnit * x.sales,
        });
      });
    });
    rows.sort((a, b) => b.netPerUnit - a.netPerUnit);
    return rows;
  }, [mps]);

  // Таблица товаров
  const productRows = useMemo(() => {
    return PRODUCTS.map(p => {
      let revenue = 0, expenses = 0, ads = 0, units = 0;
      mps.forEach(mp => {
        const rev = p[mp].price * p[mp].sales;
        const r = EXPENSE_RATIOS[mp];
        revenue += rev;
        expenses += rev * (r.commission + r.logistics + r.storage + r.ads + r.returns + r.other);
        ads += rev * r.ads;
        units += p[mp].sales;
      });
      const gross = revenue - expenses;
      const net = gross - p.cost * units;
      const margin = revenue > 0 ? (net / revenue) * 100 : 0;
      const drr = revenue > 0 ? (ads / revenue) * 100 : 0;
      const alertPrice = mps.some(mp => p[mp].price < p.minPrice);
      const alertStock = mps.some(mp => p[mp].stock < 10);
      const avgPrice = Math.round(mps.reduce((s, mp) => s + p[mp].price, 0) / mps.length);
      const totalStock = mps.reduce((s, mp) => s + p[mp].stock, 0);
      return { ...p, revenue, net, margin, drr, alertPrice, alertStock, avgPrice, totalStock };
    }).filter(p => !search || p.sku.toLowerCase().includes(search.toLowerCase()) || p.name.toLowerCase().includes(search.toLowerCase()))
      .sort((a, b) => (b as any)[sortBy] - (a as any)[sortBy]);
  }, [mps, search, sortBy]);

  // Сравнение площадок
  const comparisonRows = mps.map(mp => {
    const rev = metrics.mpData[mp];
    const r = EXPENSE_RATIOS[mp];
    const exp = rev * (r.commission + r.logistics + r.storage + r.ads + r.returns + r.other);
    const gross = rev - exp;
    const cost = PRODUCTS.reduce((s, p) => s + p.cost * p[mp].sales, 0);
    const net = gross - cost;
    return { mp, rev, exp, gross, net, margin: rev > 0 ? net / rev * 100 : 0, drr: r.ads * 100 };
  });

  const totalMargin = metrics.totalRevenue > 0 ? metrics.totalNet / metrics.totalRevenue * 100 : 0;
  const totalDrr = metrics.totalRevenue > 0 ? metrics.totalAds / metrics.totalRevenue * 100 : 0;

  return (
    <div className="p-4 max-w-[1100px] mx-auto">
      {/* Header */}
      <div className="flex justify-between items-center mb-4 flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-medium">Дашборд</h1>
          <p className="text-sm text-gray-500">20 SKU · WB · Ozon · Яндекс Маркет</p>
        </div>
        <div className="flex gap-2">
          <select className="px-3 py-1.5 border rounded-lg text-sm" value={period} onChange={e => setPeriod(e.target.value as Period)}>
            <option value="today">Сегодня</option>
            <option value="7d">7 дней</option>
            <option value="30d">30 дней</option>
          </select>
          <select className="px-3 py-1.5 border rounded-lg text-sm" value={mpFilter} onChange={e => setMpFilter(e.target.value as any)}>
            <option value="all">Все площадки</option>
            <option value="wb">Wildberries</option>
            <option value="ozon">Ozon</option>
            <option value="ym">Яндекс Маркет</option>
          </select>
        </div>
      </div>

      {/* KPI */}
      <div className="grid grid-cols-4 gap-3 mb-4">
        {[
          { label: 'Выручка', value: formatMoney(metrics.totalRevenue), wow: '+12% к прошлой неделе', wowColor: 'text-green-600' },
          { label: 'Валовая прибыль', value: formatMoney(metrics.totalGross), wow: '+5% к прошлой неделе', wowColor: 'text-green-600' },
          { label: 'Чистая прибыль', value: formatMoney(metrics.totalNet), wow: '-3% к прошлой неделе', wowColor: 'text-red-600' },
          { label: 'ДРР', value: formatPercent(totalDrr), wow: '-1.2 п.п.', wowColor: 'text-green-600' },
        ].map(kpi => (
          <div key={kpi.label} className="p-4 border rounded-xl bg-white">
            <div className="text-xs text-gray-500 mb-1">{kpi.label}</div>
            <div className="text-2xl font-medium tabular-nums">{kpi.value}</div>
            <div className={`text-xs mt-1 font-medium ${kpi.wowColor}`}>{kpi.wow}</div>
          </div>
        ))}
      </div>

      {/* Alerts */}
      <div className="mb-4">
        <h2 className="text-sm font-medium mb-2">Алерты</h2>
        <div className="flex flex-wrap gap-2">
          {alerts.length === 0 ? <span className="text-sm text-gray-400">Алертов нет</span> :
            alerts.map((a, i) => (
              <div key={i} className={`flex items-center gap-2 px-3 py-2 rounded-lg text-xs border ${a.type === 'danger' ? 'bg-red-50 border-red-500 text-red-700' : 'bg-yellow-50 border-yellow-500 text-yellow-700'}`}>
                <span className={`w-1.5 h-1.5 rounded-full ${a.type === 'danger' ? 'bg-red-500' : 'bg-yellow-500'}`} />
                {a.text}
              </div>
            ))}
        </div>
      </div>

      {/* Charts */}
      <div className="grid grid-cols-3 gap-3 mb-4">
        <div className="col-span-2 p-4 border rounded-xl bg-white">
          <h3 className="text-sm font-medium mb-3">Выручка по площадкам</h3>
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
              <XAxis dataKey="day" tick={{ fontSize: 10 }} />
              <YAxis tick={{ fontSize: 10 }} />
              <Tooltip />
              {mps.map(mp => <Line key={mp} type="monotone" dataKey={mp} stroke={MP_COLORS[mp]} strokeWidth={2} dot={false} />)}
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div className="p-4 border rounded-xl bg-white">
          <h3 className="text-sm font-medium mb-3">Доля выручки</h3>
          <ResponsiveContainer width="100%" height={140}>
            <PieChart>
              <Pie data={shareData} cx="50%" cy="50%" innerRadius={40} outerRadius={55} dataKey="value" stroke="none">
                {shareData.map((entry, i) => <Cell key={i} fill={entry.color} />)}
              </Pie>
            </PieChart>
          </ResponsiveContainer>
          <div className="mt-2 space-y-1">
            {shareData.map(d => (
              <div key={d.name} className="flex justify-between text-xs">
                <span className="flex items-center gap-1.5 text-gray-600"><span className="w-2 h-2 rounded-full" style={{ background: d.color }} />{d.name}</span>
                <span className="font-medium">{formatMoney(d.value)}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Expenses + DRR */}
      <div className="grid grid-cols-2 gap-3 mb-4">
        <div className="p-4 border rounded-xl bg-white">
          <h3 className="text-sm font-medium mb-3">Структура расходов</h3>
          <div className="flex items-center gap-4">
            <ResponsiveContainer width={130} height={130}>
              <PieChart>
                <Pie data={expenseData} cx="50%" cy="50%" innerRadius={35} outerRadius={50} dataKey="value" stroke="none">
                  {expenseData.map((e, i) => <Cell key={i} fill={e.color} />)}
                </Pie>
              </PieChart>
            </ResponsiveContainer>
            <div className="flex-1 space-y-1">
              {expenseData.map((e, i) => (
                <div key={e.name} className="flex justify-between text-xs">
                  <span className="flex items-center gap-1.5 text-gray-600"><span className="w-2 h-2 rounded-sm" style={{ background: e.color }} />{e.name}</span>
                  <span className="font-medium">{formatMoney(e.value)}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
        <div className="p-4 border rounded-xl bg-white">
          <h3 className="text-sm font-medium mb-3">ДРР по площадкам</h3>
          <div className="space-y-3">
            {mps.map(mp => {
              const drr = metrics.mpData[mp] > 0 ? (metrics.mpData[mp] * EXPENSE_RATIOS[mp].ads) / metrics.mpData[mp] * 100 : 0;
              return (
                <div key={mp}>
                  <div className="flex justify-between text-xs mb-1">
                    <span className="text-gray-600">{MP_NAMES[mp]}</span>
                    <span className={`font-medium ${drr <= 10 ? 'text-green-600' : 'text-red-600'}`}>{formatPercent(drr)}</span>
                  </div>
                  <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
                    <div className="h-full rounded-full" style={{ width: `${Math.min(drr / 15 * 100, 100)}%`, background: MP_COLORS[mp] }} />
                  </div>
                </div>
              );
            })}
          </div>
          <div className="mt-3 pt-2 border-t text-xs text-gray-400">Целевой ДРР: ≤ 10%</div>
        </div>
      </div>

      {/* Unit Economics */}
      <div className="p-4 border rounded-xl bg-white overflow-x-auto mb-4">
        <div className="flex justify-between items-center mb-3">
          <h3 className="text-sm font-medium">Заработок с 1 товара</h3>
          <span className="text-xs text-gray-400">Чистая прибыль за 1 проданную единицу</span>
        </div>
        <table className="w-full text-xs min-w-[700px]">
          <thead>
            <tr className="border-b text-gray-400">
              <th className="text-left py-2 font-normal">Товар</th>
              <th className="text-left py-2 font-normal">Площадка</th>
              <th className="text-right py-2 font-normal">Цена</th>
              <th className="text-right py-2 font-normal">Себестоимость</th>
              <th className="text-right py-2 font-normal">Расходы МП</th>
              <th className="text-right py-2 font-normal">Чистая / 1 шт</th>
              <th className="text-right py-2 font-normal">Продано</th>
              <th className="text-right py-2 font-normal">Всего чистая</th>
            </tr>
          </thead>
          <tbody>
            {unitRows.map((r, i) => (
              <tr key={i} className="border-b">
                <td className="py-2 text-gray-900 truncate max-w-[180px]">{r.name}</td>
                <td className="py-2">
                  <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium" style={{ background: MP_COLORS[r.mp] + '15', color: MP_COLORS[r.mp] }}>
                    <span className="w-1 h-1 rounded-full" style={{ background: MP_COLORS[r.mp] }} />{MP_NAMES[r.mp]}
                  </span>
                </td>
                <td className="py-2 text-right tabular-nums">{Math.round(r.price)}₽</td>
                <td className="py-2 text-right tabular-nums text-gray-500">{Math.round(r.cost)}₽</td>
                <td className="py-2 text-right tabular-nums text-gray-500">{Math.round(r.expensePerUnit)}₽</td>
                <td className="py-2 text-right">
                  <span className={`inline-block px-2 py-0.5 rounded-md font-medium ${r.netPerUnit > 0 ? 'bg-green-50 text-green-600' : 'bg-red-50 text-red-600'}`}>
                    {r.netPerUnit > 0 ? '+' : ''}{Math.round(r.netPerUnit)}₽
                  </span>
                </td>
                <td className="py-2 text-right tabular-nums text-gray-500">{r.sales} шт</td>
                <td className="py-2 text-right tabular-nums font-medium">{formatMoney(r.totalNet)}</td>
              </tr>
            ))}
            {unitRows.length > 0 && (
              <tr className="border-t-2 font-medium">
                <td className="py-2 text-gray-900" colSpan={5}>Средняя чистая прибыль с 1 товара</td>
                <td className="py-2 text-right">
                  <span className={`inline-block px-2 py-0.5 rounded-md font-medium ${unitRows.reduce((s, r) => s + r.netPerUnit, 0) / unitRows.length > 0 ? 'bg-green-50 text-green-600' : 'bg-red-50 text-red-600'}`}>
                    +{Math.round(unitRows.reduce((s, r) => s + r.netPerUnit, 0) / unitRows.length)}₽
                  </span>
                </td>
                <td className="py-2 text-right text-gray-400" />
                <td className="py-2 text-right tabular-nums">{formatMoney(unitRows.reduce((s, r) => s + r.totalNet, 0))}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Products Table */}
      <div className="p-4 border rounded-xl bg-white overflow-x-auto mb-4">
        <div className="flex justify-between items-center mb-3 flex-wrap gap-2">
          <h3 className="text-sm font-medium">Товары</h3>
          <div className="flex gap-2">
            <input className="px-3 py-1 border rounded-lg text-sm w-56" placeholder="Поиск по SKU или названию..." value={search} onChange={e => setSearch(e.target.value)} />
            <select className="px-2 py-1 border rounded-lg text-sm" value={sortBy} onChange={e => setSortBy(e.target.value as any)}>
              <option value="revenue">По выручке</option>
              <option value="net">По чистой прибыли</option>
              <option value="margin">По марже</option>
              <option value="drr">По ДРР</option>
            </select>
          </div>
        </div>
        <table className="w-full text-xs min-w-[800px]">
          <thead>
            <tr className="border-b text-gray-400">
              <th className="text-left py-2 font-normal">SKU</th>
              <th className="text-left py-2 font-normal">Название</th>
              <th className="text-right py-2 font-normal">Выручка</th>
              <th className="text-right py-2 font-normal">Чистая</th>
              <th className="text-right py-2 font-normal">Маржа</th>
              <th className="text-right py-2 font-normal">ДРР</th>
              <th className="text-right py-2 font-normal">Цена</th>
              <th className="text-right py-2 font-normal">Мин. цена</th>
              <th className="text-right py-2 font-normal">Остаток</th>
              <th className="text-center py-2 font-normal">Статус</th>
            </tr>
          </thead>
          <tbody>
            {productRows.map(p => (
              <tr key={p.sku} className="border-b">
                <td className="py-2 text-gray-500 font-mono text-[11px]">{p.sku}</td>
                <td className="py-2 text-gray-900 truncate max-w-[200px]">{p.name}</td>
                <td className="py-2 text-right tabular-nums">{formatMoney(p.revenue)}</td>
                <td className="py-2 text-right tabular-nums font-medium">{formatMoney(p.net)}</td>
                <td className={`py-2 text-right font-medium ${p.margin >= 25 ? 'text-green-600' : p.margin >= 15 ? 'text-gray-900' : 'text-red-600'}`}>{formatPercent(p.margin)}</td>
                <td className={`py-2 text-right font-medium ${p.drr <= 10 ? 'text-green-600' : p.drr <= 15 ? 'text-yellow-600' : 'text-red-600'}`}>{formatPercent(p.drr)}</td>
                <td className="py-2 text-right tabular-nums text-gray-500">{p.avgPrice}₽</td>
                <td className="py-2 text-right tabular-nums text-gray-400">{p.minPrice}₽</td>
                <td className={`py-2 text-right tabular-nums ${p.totalStock < 20 ? 'text-red-600 font-medium' : 'text-gray-500'}`}>{p.totalStock}</td>
                <td className="py-2 text-center">
                  {p.alertPrice && <span className="inline-block w-1.5 h-1.5 rounded-full bg-red-500 mx-0.5" title="Цена ниже минимальной" />}
                  {p.alertStock && <span className="inline-block w-1.5 h-1.5 rounded-full bg-yellow-500 mx-0.5" title="Низкий остаток" />}
                  {!p.alertPrice && !p.alertStock && <span className="inline-block w-1.5 h-1.5 rounded-full bg-green-500 mx-0.5" title="OK" />}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Comparison */}
      <div className="p-4 border rounded-xl bg-white overflow-x-auto">
        <h3 className="text-sm font-medium mb-3">Сравнение площадок</h3>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-gray-400">
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
            {comparisonRows.map(r => (
              <tr key={r.mp} className="border-b">
                <td className="py-2">
                  <span className="flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full" style={{ background: MP_COLORS[r.mp] }} />
                    <span className="font-medium">{MP_NAMES[r.mp]}</span>
                  </span>
                </td>
                <td className="py-2 text-right tabular-nums">{formatMoney(r.rev)}</td>
                <td className="py-2 text-right tabular-nums">{formatMoney(r.exp)}</td>
                <td className="py-2 text-right tabular-nums">{formatMoney(r.gross)}</td>
                <td className="py-2 text-right tabular-nums font-medium">{formatMoney(r.net)}</td>
                <td className={`py-2 text-right font-medium ${r.margin >= 25 ? 'text-green-600' : r.margin >= 15 ? 'text-gray-900' : 'text-red-600'}`}>{formatPercent(r.margin)}</td>
                <td className={`py-2 text-right font-medium ${r.drr <= 10 ? 'text-green-600' : 'text-red-600'}`}>{formatPercent(r.drr)}</td>
              </tr>
            ))}
            <tr className="border-t-2 font-medium">
              <td className="py-2">Итого</td>
              <td className="py-2 text-right tabular-nums">{formatMoney(metrics.totalRevenue)}</td>
              <td className="py-2 text-right tabular-nums">{formatMoney(metrics.totalExpenses)}</td>
              <td className="py-2 text-right tabular-nums">{formatMoney(metrics.totalGross)}</td>
              <td className="py-2 text-right tabular-nums">{formatMoney(metrics.totalNet)}</td>
              <td className="py-2 text-right">{formatPercent(totalMargin)}</td>
              <td className="py-2 text-right">{formatPercent(totalDrr)}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}
