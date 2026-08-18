import { useEffect, useState } from 'react';
import { shops } from '../lib/api';

const MP_NAMES = { wb: 'Wildberries', ozon: 'Ozon', ym: 'Яндекс Маркет' };
const MP_COLORS = { wb: 'bg-blue-100 text-blue-700', ozon: 'bg-purple-100 text-purple-700', ym: 'bg-red-100 text-red-700' };

function formatDate(v) {
  if (!v) return 'никогда';
  const d = new Date(v);
  return d.toLocaleString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export default function Shops() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [syncingId, setSyncingId] = useState(null);
  const [syncResult, setSyncResult] = useState(null);
  const [togglingId, setTogglingId] = useState(null);

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      const data = await shops.list();
      setItems(data);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const handleSync = async (shop) => {
    setSyncingId(shop.id);
    setSyncResult(null);
    try {
      const result = await shops.sync(shop.id);
      setSyncResult({ shop: shop.name, ...result });
      await load();
    } catch (e) {
      setSyncResult({ shop: shop.name, status: 'error', message: e.message });
    } finally {
      setSyncingId(null);
    }
  };

  const handleToggle = async (shop) => {
    setTogglingId(shop.id);
    try {
      await shops.toggleSync(shop.id);
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setTogglingId(null);
    }
  };

  if (loading) return <div className="text-center py-20 text-gray-500">Загрузка...</div>;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Магазины и синхронизация</h1>
          <p className="text-sm text-gray-500">Управление подключёнными площадками и ручной запуск синхронизации</p>
        </div>
        <button
          onClick={load}
          className="px-4 py-2 border rounded-lg bg-white text-sm font-medium hover:bg-gray-50"
        >
          Обновить
        </button>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-500 text-red-700 px-4 py-3 rounded-lg text-sm">
          {error}
        </div>
      )}

      {syncResult && (
        <div
          className={`border px-4 py-4 rounded-lg text-sm ${
            syncResult.status === 'success'
              ? 'bg-green-50 border-green-500 text-green-700'
              : 'bg-red-50 border-red-500 text-red-700'
          }`}
        >
          <div className="font-medium text-base">{syncResult.shop}</div>
          <div className="mt-1">Общий статус: {syncResult.status}</div>
          {syncResult.message && <div className="mt-1">{syncResult.message}</div>}
          <div className="mt-3 grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-2">
            {[
              { key: 'orders', label: 'Заказы' },
              { key: 'stocks', label: 'Остатки' },
              { key: 'adverts', label: 'Реклама' },
              { key: 'prices', label: 'Цены' },
              { key: 'finance', label: 'Расходы' },
            ].map(({ key, label }) => {
              const section = syncResult.sections?.[key] || syncResult[key] || { status: 'unknown', count: 0, message: '' };
              const isSuccess = section.status === 'success';
              const isError = section.status === 'error';
              return (
                <div key={key} className="bg-white border rounded-lg px-3 py-2">
                  <div className="text-xs text-gray-500">{label}</div>
                  <div className="flex items-center gap-1.5 mt-1">
                    <span className={`w-2 h-2 rounded-full ${isSuccess ? 'bg-green-500' : isError ? 'bg-red-500' : 'bg-gray-400'}`} />
                    <span className="font-medium text-gray-900">{section.count}</span>
                  </div>
                  {section.message && <div className="text-xs text-gray-500 mt-1 truncate" title={section.message}>{section.message}</div>}
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-gray-500 border-b">
            <tr>
              <th className="text-left py-3 px-4 font-normal">Площадка</th>
              <th className="text-left py-3 px-4 font-normal">Название</th>
              <th className="text-left py-3 px-4 font-normal">Последняя синхронизация</th>
              <th className="text-left py-3 px-4 font-normal">Автосинхронизация</th>
              <th className="text-right py-3 px-4 font-normal">Действия</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 && (
              <tr>
                <td colSpan="5" className="py-8 text-center text-gray-400">
                  Магазины не подключены
                </td>
              </tr>
            )}
            {items.map((shop) => {
              const mp = shop.marketplace;
              return (
                <tr key={shop.id} className="border-b last:border-b-0">
                  <td className="py-3 px-4">
                    <span className={`inline-flex items-center px-2.5 py-1 rounded-md text-xs font-medium ${MP_COLORS[mp] || 'bg-gray-100 text-gray-700'}`}>
                      {MP_NAMES[mp] || mp}
                    </span>
                  </td>
                  <td className="py-3 px-4 font-medium text-gray-900">{shop.name}</td>
                  <td className="py-3 px-4 text-gray-600">{formatDate(shop.last_sync_at)}</td>
                  <td className="py-3 px-4">
                    <button
                      onClick={() => handleToggle(shop)}
                      disabled={togglingId === shop.id}
                      className={`relative inline-flex h-5 w-9 items-center rounded-full transition ${
                        shop.sync_enabled ? 'bg-blue-600' : 'bg-gray-300'
                      }`}
                    >
                      <span
                        className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition ${
                          shop.sync_enabled ? 'translate-x-5' : 'translate-x-1'
                        }`}
                      />
                    </button>
                  </td>
                  <td className="py-3 px-4 text-right">
                    <button
                      onClick={() => handleSync(shop)}
                      disabled={syncingId === shop.id}
                      className="inline-flex items-center gap-2 px-3 py-1.5 bg-blue-600 text-white rounded-lg text-xs font-medium hover:bg-blue-700 disabled:opacity-60 disabled:cursor-not-allowed"
                    >
                      {syncingId === shop.id ? (
                        <>
                          <span className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                          Синхронизация...
                        </>
                      ) : (
                        'Синхронизировать'
                      )}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="bg-gray-50 rounded-xl p-5 text-sm text-gray-600 space-y-2">
        <p className="font-medium text-gray-900">Справка</p>
        <ul className="list-disc list-inside space-y-1">
          <li><strong>Синхронизировать</strong> — загружает заказы, остатки, расходы и рекламу за последние сутки.</li>
          <li><strong>Автосинхронизация</strong> — фоновая синхронизация каждые 4 часа (Celery beat).</li>
          <li>После изменений в адаптерах (например, загрузка расходов) рекомендуется нажать «Синхронизировать» повторно.</li>
        </ul>
      </div>
    </div>
  );
}
