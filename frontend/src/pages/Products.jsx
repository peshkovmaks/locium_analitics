import { useEffect, useState } from 'react';
import { products } from '../lib/api';

function formatMoney(v) {
  if (v === undefined || v === null) return '—';
  const n = typeof v === 'string' ? parseFloat(v) : Number(v);
  return n.toLocaleString('ru-RU', { style: 'currency', currency: 'RUB', maximumFractionDigits: 2 });
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
              <th className="px-6 py-3 font-medium text-center">Действие</th>
            </tr>
          </thead>
          <tbody>
            {list.map((p) => {
              const sku = getSku(p); // ← вот тут берём канонический SKU
              return (
                <tr key={p.id} className="border-b border-gray-50 hover:bg-gray-50">
                  <td className="px-4 py-3">
                    <input
                      type="checkbox"
                      checked={selected.has(sku)}
                      onChange={() => toggleSku(sku)}
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
                    {editingSku === sku ? (
                      <div className="flex items-center justify-center gap-2">
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
                        onClick={() => {
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
    </div>
  );
}  