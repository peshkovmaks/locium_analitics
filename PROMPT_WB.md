# Промпт: дальнейшая работа по Wildberries

## Что уже сделано

1. Маппинг finance-отчёта WB обновлён в `backend/app/adapters/wildberries.py`:
   - `commission` = `ppvzSalesCommission` + `ppvzReward`.
   - `logistics` = `deliveryService` − `rebillLogisticCost`.
   - `other` = `deduction` + `penalty` + `paidAcceptance` (с fallback на `acceptance`).
   - `storage` ищет `paidStorage`/`storageFee`, `deliveryService` ищет `deliveryService`/`deliveryRub`.
2. Unit-тесты обновлены и проходят (`backend/tests/test_wildberries_adapter.py` — 10/10).
3. Docker compose перезапущен с актуальным `backend/.env`, проблема `401 Unauthorized` в sync_logs решена.
4. Сделан один успешный finance-запрос: за первый 30-дневный чанк получено **1912 строк** (sync_log `2026-08-28 20:39:13`).
5. Создан экономный скрипт `backend/scripts/sync_wb_orders_sales.py`, который синхронизирует только `orders` и `sales`, пропуская чанки, где данные уже есть.

## Текущее состояние загрузки

- **Май–июнь 2026:** `sales` загружено **1092 строки**.
- **Июль 2026:** не загружено (rate limit).
- **Август 2026:** продажи за август уже есть в БД, но не обновлялись в этом заходе.
- **Finance:** за май–июнь загружено 1912 строк; расходы пока не распределены по продажам за отсутствием sales за тот же период.
- **Balance / adverts / prices:** не синхронизированы, так как тратят rate limit; не критичны для сверки основных цифр.

## Что нужно сделать

### 1. Догрузить orders/sales за 90 дней
- Запустить `backend/scripts/sync_wb_orders_sales.py` через cron или вручную, когда WB Statistics API снимет rate limit.
- Скрипт пропускает чанки с уже загруженными данными, поэтому повторные запуски не сжигают лимит впустую.
- Запускать только тогда, когда тестовый запрос к `/api/v1/supplier/orders` возвращает `200`. Если `429` — отложить.

### 2. Загрузить finance report за 90 дней
- Когда orders/sales за весь период будут в БД, сделать **один** запрос к `/api/finance/v1/sales-reports/detailed` за последние 90 дней.
- Распределить расходы по `Sale` через `SyncService._update_finance_data`.
- Если WB finance API в rate limit (`retry-after` > 0) — отложить.

### 3. Сверить цифры с ЛК WB
- За последние 7 дней сравнить:
  - Количество заказов.
  - Выручку.
  - Сумму комиссии, логистики, хранения, рекламы.
  - Валовую прибыль и ДРР.
- Целевое расхождение — в пределах нескольких процентов.

### 4. Доработки (опционально, после сверки)
- Рекламный API WB (`get_adverts`) — проверить и подключить распределение рекламных расходов.
- Остатки (`get_stocks`) и цены (`get_prices`) — включить, если потребуются для дашборда.
- Дополнить unit-тесты для граничных случаев finance-отчёта.

## Полезные файлы

- `backend/app/adapters/wildberries.py` — адаптер WB.
- `backend/app/services/sync_service.py` — синхронизация и распределение расходов.
- `backend/app/resync_wb.py` — старая полная синхронизация за 90 дней (сжигает лимиты, лучше использовать `sync_wb_orders_sales.py`).
- `backend/scripts/sync_wb_orders_sales.py` — актуальный экономный скрипт.
- `backend/tests/test_wildberries_adapter.py` — unit-тесты.

## Ближайший шаг

1. Дождаться снятия rate limit WB Statistics API.
2. Запустить `backend/scripts/sync_wb_orders_sales.py`.
3. После успешной загрузки orders/sales дождаться снятия rate limit WB Finance API и сделать один finance-запрос за 90 дней.
4. Сверить цифры за последние 7 дней с ЛК WB.
