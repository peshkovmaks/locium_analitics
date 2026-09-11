import { useEffect, useState } from 'react';
import { products } from '../lib/api';

function formatMoney(v) {
  if (v === undefined || v === null) return '—';
  const n = typeof v === 'string' ? parseFloat(v) : Number(v);
  return n.toLocaleString('ru-RU', { style: 'currency', currency: 'RUB', maximumFractionDigits: 2 });
}

function formatDate(v) {
  if (!v) return '';
  const d = new Date(v);
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', year: 'numeric' });
}

const REC_BADGES = {
  raise: { label: 'Повысить', cls: 'bg-amber-100 text-amber-700' },
  keep: { label: 'Оставить', cls: 'bg-green-100 text-green-700' },
  lower: { label: 'Снизить', cls: 'bg-sky-100 text-sky-700' },
};

function RecommendationBadge({ rec }) {
  if (!rec || !REC_BADGES[rec.action]) return <span className="text-gray-400">—</span>;
  const badge = REC_BADGES[rec.action];
  const title = rec.recommended_price != null
    ? `Рекомендуемая цена: ${formatMoney(rec.recommended_price)}`
    : 'Недостаточно данных для расчёта';
  return (
    <span
      title={title}
      className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${badge.cls}`}
    >
      {badge.label}
      {rec.recommended_price != null && (
        <span className="ml-1 opacity-70">{formatMoney(rec.recommended_price)}</span>
      )}
    </span>
  );
}

function PriceSparkline({ points }) {
  const W = 520;
  const H = 140;
  const PAD = 10;

  if (!points || points.length === 0) {
    return <div className="text-sm text-gray-500 py-8 text-center">Истории цен пока нет — она появится после синхронизаций.</div>;
  }

  const prices = points.map((p) => Number(p.price));
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const span = max - min || 1;
  const stepX = points.length > 1 ? (W - PAD * 2) / (points.length - 1) : 0;
  const coords = points.map((p, i) => [
    PAD + i * stepX,
    H - PAD - ((Number(p.price) - min) / span) * (H - PAD * 2),
  ]);
  const path = coords
    .map((c, i) => `${i === 0 ? 'M' : 'L'}${c[0].toFixed(1)},${c[1].toFixed(1)}`)
    .join(' ');
  const last = coords[coords.length - 1];

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full">
        <path d={path} fill="none" stroke="#2563eb" strokeWidth="2" />
        {coords.map((c, i) => (
          <circle key={i} cx={c[0]} cy={c[1]} r="2.5" fill="#2563eb">
            <title>{`${formatMoney(points[i].price)} · ${formatDate(points[i].created_at)}`}</title>
          </circle>
        ))}
        {last && <circle cx={last[0]} cy={last[1]} r="4" fill="#1d4ed8" />}
      </svg>
      <div className="flex justify-between text-xs text-gray-500 mt-1">
        <span>
          {formatDate(points[0].created_at)} — {formatDate(points[points.length - 1].created_at)}
        </span>
        <span>
          мин {formatMoney(min)} · макс {formatMoney(max)}
        </span>
      </div>
    </div>
  );
}

function PriceHistoryModal({ product, onClose }) {
  const [points, setPoints] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    products
      .priceHistory(product.id)
      .then((data) => {
        if (!cancelled) setPoints(data);
      })
      .catch((e) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [product.id]);

  return (
    <div
      className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-xl shadow-lg max-w-2xl w-full p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between mb-4">
          <div>
            <h2 className="text-lg font-bold text-gray-900">{product.name}</h2>
            <p className="text-xs font-mono text-gray-500">{product.canonical_sku || product.sku}</p>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 text-xl leading-none"
            aria-label="Закрыть"
          >
            ×
          </button>
        </div>
        {error && <div className="text-sm text-red-600 mb-3">Ошибка: {error}</div>}
        {points === null ? (
          <div className="text-sm text-gray-500 py-8 text-center">Загрузка...</div>
        ) : (
          <PriceSparkline points={points} />
        )}
      </div>
    </div>
  );
}

export default function Products() {
  const [list, setList] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [editingSku, setEditingSku] = useState(null);
  const [editValue, setEditValue] = useState('');
  const [saving, setSaving] = useState(false);
  const [selected, setSelected] = useState(new Set());
  const [merging, setMerging] = useState(false);
  const [historyProduct, setHistoryProduct] = useState(null);

  useEffect(() => {
    load();
  }, []);

  async function load() {
    setLoading(true);
    try {
      const data = await products.list();
      setList(data);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  async function saveCost(sku) {
    setSaving(true);
    try {
      await products.updateCost(sku, parseFloat(editValue));
      setEditingSku(null);
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }

  function toggleSku(sku) {
    const next = new Set(selected);
    if (next.has(sku)) {
      next.delete(sku);
    } else {
      next.add(sku);
    }
    setSelected(next);
  }

  async function mergeSelected() {
    if (selected.size < 2) return;
    const skus = Array.from(selected);
    const target = skus[0];
    const sources = skus.slice(1);
    if (!window.confirm(`Объединить ${sources.length} товар(а) в «${target}»?`)) return;
    setMerging(true);
    try {
      await products.merge(sources, target);
      setSelected(new Set());
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setMerging(false);
    }
  }

  // Канонический SKU: используем canonical_sku, если есть, иначе sku
  const getSku = (p) => p.canonical_sku || p.sku;

  if (loading) return <div className="text-center py-20 text-gray-500">Загрузка...</div>;
  if (error) return <div className="text-center py-20 text-red-600">Ошибка: {error}</div>;

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
      <div className="p-6 border-b border-gray-100 flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-900">Товары</h1>
        <div className="flex items-center gap-4">
          {selected.size >= 2 && (
            <button
              onClick={mergeSelected}
              disabled={merging}
              className="px-3 py-1.5 bg-purple-600 text-white rounded text-xs font-medium hover:bg-purple-700 disabled:opacity-50"
            >
              {merging ? '...' : `Объединить выбранные (${selected.size})`}
            </button>
          )}
          <span className="text-sm text-gray-500">Всего: {list.length}</span>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-gray-500 border-b border-gray-100 bg-gray-50">
              <th className="px-4 py-3 font-medium w-10">
                <input
                  type="checkbox"
                  checked={list.length > 0 && selected.size === list.length}
                  onChange={(e) => {
                    if (e.target.checked) {
                      setSelected(new Set(list.map(getSku)));
                    } else {
                      setSelected(new Set());
                    }
                  }}
                  className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                />
              </th>
              <th className="px-6 py-3 font-medium">SKU</th>
              <th className="px-6 py-3 font-medium">Название</th>
              <th className="px-6 py-3 font-medium text-right">Себестоимость</th>
              <th className="px-6 py-3 font-medium text-right">Мин. цена</th>
              <th className="px-6 py-3 font-medium text-center">Рекомендация</th>
              <th className="px-6 py-3 font-medium text-center">Действие</th>
            </tr>
          </thead>
          <tbody>
            {list.map((p) => {
              const sku = getSku(p); // ← вот тут берём канонический SKU
              return (
                <tr
                  key={p.id}
                  className="border-b border-gray-50 hover:bg-gray-50 cursor-pointer"
                  onClick={() => setHistoryProduct(p)}
                >
                  <td className="px-4 py-3">
                    <input
                      type="checkbox"
                      checked={selected.has(sku)}
                      onChange={() => toggleSku(sku)}
                      onClick={(e) => e.stopPropagation()}
                      className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                    />
                  </td>
                  <td className="px-6 py-3 font-mono text-xs text-gray-600">{sku}</td>
                  <td className="px-6 py-3 font-medium text-gray-900">{p.name}</td>
                  <td className="px-6 py-3 text-right">
                    {editingSku === sku ? (
                      <input
                        type="number"
                        step="0.01"
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        className="w-32 px-2 py-1 border border-gray-300 rounded text-right focus:outline-none focus:ring-2 focus:ring-blue-500"
                        autoFocus
                      />
                    ) : (
                      <span>{formatMoney(p.cost_price)}</span>
                    )}
                  </td>
                  <td className="px-6 py-3 text-right">{formatMoney(p.min_price)}</td>
                  <td className="px-6 py-3 text-center">
                    <RecommendationBadge rec={p.price_recommendation} />
                  </td>
                  <td className="px-6 py-3 text-center">
                    {editingSku === sku ? (
                      <div
                        className="flex items-center justify-center gap-2"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <button
                          onClick={() => saveCost(sku)}
                          disabled={saving}
                          className="px-3 py-1 bg-blue-600 text-white rounded text-xs font-medium hover:bg-blue-700 disabled:opacity-50"
                        >
                          {saving ? '...' : 'Сохранить'}
                        </button>
                        <button
                          onClick={() => setEditingSku(null)}
                          className="px-3 py-1 bg-gray-200 text-gray-700 rounded text-xs font-medium hover:bg-gray-300"
                        >
                          Отмена
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setEditingSku(sku);
                          setEditValue(p.cost_price);
                        }}
                        className="px-3 py-1 text-blue-600 hover:text-blue-700 text-xs font-medium"
                      >
                        Изменить
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {list.length === 0 && (
        <div className="p-12 text-center text-gray-500">
          Товары не найдены. Выполните синхронизацию магазина — товары создадутся автоматически.
        </div>
      )}
      {historyProduct && (
        <PriceHistoryModal product={historyProduct} onClose={() => setHistoryProduct(null)} />
      )}
    </div>
  );
}  
