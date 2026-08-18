import { useEffect, useState } from 'react';
import { shops } from '../lib/api';

const MP_NAMES = { wb: 'Wildberries', ozon: 'Ozon', ym: 'Яндекс Маркет' };
const MP_COLORS = { wb: 'bg-blue-100 text-blue-700', ozon: 'bg-purple-100 text-purple-700', ym: 'bg-red-100 text-red-700' };
const SECTIONS = [
  { key: 'orders', label: 'Заказы' },
  { key: 'stocks', label: 'Остатки' },
  { key: 'adverts', label: 'Реклама' },
  { key: 'prices', label: 'Цены' },
  { key: 'finance', label: 'Расходы' },
];

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

function SectionBadge({ section }) {
  const status = section?.status || 'unknown';
  const count = section?.count ?? 0;
  const isSuccess = status === 'success';
  const isError = status === 'error';
  return (
    <div className="flex items-center gap-1.5 text-xs">
      <span className={`w-2 h-2 rounded-full ${isSuccess ? 'bg-green-500' : isError ? 'bg-red-500' : 'bg-gray-400'}`} />
      <span className="text-gray-600">{count}</span>
    </div>
  );
}

function ShopRow({ shop, syncingId, togglingId, lastLog, onSync, onToggle }) {
  const mp = shop.marketplace;
  const sections = lastLog?.sections || {};

  return (
    <div className="border-b last:border-b-0 p-4">
      <div className="flex flex-col lg:flex-row lg:items-start gap-4">
        <div className="flex-1 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div>
            <div className="text-xs text-gray-500 mb-1">Площадка</div>
            <span className={`inline-flex items-center px-2.5 py-1 rounded-md text-xs font-medium ${MP_COLORS[mp] || 'bg-gray-100 text-gray-700'}`}>
              {MP_NAMES[mp] || mp}
            </span>
          </div>
          <div>
            <div className="text-xs text-gray-500 mb-1">Название</div>
            <div className="font-medium text-gray-900">{shop.name}</div>
          </div>
          <div>
            <div className="text-xs text-gray-500 mb-1">Последняя синхронизация</div>
            <div className="text-gray-600">{formatDate(shop.last_sync_at)}</div>
            {lastLog && (
              <div className="text-xs text-gray-400 mt-0.5">
                Статус: <span className={lastLog.status === 'success' ? 'text-green-600' : lastLog.status === 'error' ? 'text-red-600' : 'text-gray-600'}>{lastLog.status}</span>
              </div>
            )}
          </div>
          <div>
            <div className="text-xs text-gray-500 mb-1">Автосинхронизация</div>
            <button
              onClick={() => onToggle(shop)}
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
          </div>
        </div>
        <div className="flex items-start gap-3">
          <button
            onClick={() => onSync(shop)}
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
        </div>
      </div>

      <div className="mt-3 grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-2">
        {SECTIONS.map(({ key, label }) => {
          const section = sections[key] || { status: 'unknown', count: 0, message: '' };
          const isSuccess = section.status === 'success';
          const isError = section.status === 'error';
          return (
            <div key={key} className="bg-gray-50 border rounded-lg px-3 py-2">
              <div className="text-xs text-gray-500">{label}</div>
              <div className="flex items-center justify-between mt-1">
                <SectionBadge section={section} />
                <span className={`text-[10px] font-medium uppercase ${isSuccess ? 'text-green-600' : isError ? 'text-red-600' : 'text-gray-400'}`}>
                  {section.status}
                </span>
              </div>
              {section.message && (
                <div className="text-[10px] text-gray-500 mt-1 truncate" title={section.message}>
                  {section.message}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function Shops() {
  const [items, setItems] = useState([]);
  const [logs, setLogs] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [syncingId, setSyncingId] = useState(null);
  const [syncResult, setSyncResult] = useState(null);
  const [togglingId, setTogglingId] = useState(null);

  const loadLogs = async (shopsList) => {
    const entries = {};
    await Promise.all(
      shopsList.map(async (shop) => {
        try {
          const data = await shops.syncLogs(shop.id, 1);
          entries[shop.id] = data[0] || null;
        } catch {
          entries[shop.id] = null;
        }
      })
    );
    setLogs(entries);
  };

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      const data = await shops.list();
      setItems(data);
      await loadLogs(data);
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
            {SECTIONS.map(({ key, label }) => {
              const section = syncResult.sections?.[key] || syncResult[key] || { status: 'unknown', count: 0, message: '' };
              const isSuccess = section.status === 'success';
              const isError = section.status === 'error';
              return (
                <div key={key} className="bg-white border rounded-lg px-3 py-2">
                  <div className="text-xs text-gray-500">{label}</div>
                  <div className="flex items-center justify-between mt-1">
                    <div className="flex items-center gap-1.5">
                      <span className={`w-2 h-2 rounded-full ${isSuccess ? 'bg-green-500' : isError ? 'bg-red-500' : 'bg-gray-400'}`} />
                      <span className="font-medium text-gray-900">{section.count}</span>
                    </div>
                    <span className={`text-[10px] font-medium uppercase ${isSuccess ? 'text-green-600' : isError ? 'text-red-600' : 'text-gray-400'}`}>
                      {section.status}
                    </span>
                  </div>
                  {section.message && <div className="text-xs text-gray-500 mt-1 truncate" title={section.message}>{section.message}</div>}
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
        {items.length === 0 ? (
          <div className="py-8 text-center text-gray-400 text-sm">Магазины не подключены</div>
        ) : (
          items.map((shop) => (
            <ShopRow
              key={shop.id}
              shop={shop}
              syncingId={syncingId}
              togglingId={togglingId}
              lastLog={logs[shop.id]}
              onSync={handleSync}
              onToggle={handleToggle}
            />
          ))
        )}
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
