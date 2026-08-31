# Промпт: доделать расходы Яндекс Маркета

## Что уже сделано (31.08.2026)

1. **Заказы ЯМ синхронизированы за 210 дней** (01.02.2026 – 31.08.2026) через `backend/resync_ym.py 210`.
   - В БД: **1097 уникальных заказов**, из них **1042 non-return**.
   - В ЛК ЯМ: **1042 доставленных заказа** — совпадает.
2. **Исправлена пагинация заказов** в `backend/app/adapters/yandex_market.py::_fetch_orders`:
   - Используются параметры `fromDate`/`toDate` (вместо `dateFrom`/`dateTo`).
   - `limit=50` (API игнорирует `limit>50`).
3. **Исправлен расчёт выручки и субсидий**:
   - `price` / `revenue` = `buyerPrice * quantity` (цена после скидок, которую заплатил покупатель).
   - `marketplace_discount` = `order.subsidies`, распределённые пропорционально `buyerPrice * quantity` по товарам.
   - `actual revenue` = `revenue + marketplace_discount`.
4. **Сверка по заказам и выручке**:
   - `revenue` (buyer paid) non-return = **988 838 ₽**.
   - `marketplace_discount` non-return = **600 934 ₽**.
   - `actual revenue` non-return = **1 589 772 ₽**.
   - В ЛК: **Выручка = 1 558 025 ₽**, **Доставленные заказы = 1042**.
   - Расхождение по выручке ~**2 %** (~31 747 ₽). Приемлемо, но можно ещё уточнить.
5. **Chunking finance report** реализован в `backend/app/adapters/yandex_market.py::get_finance_report`:
   - Период делится на чанки по 28 дней.
   - Между чанками `asyncio.sleep(120)` из-за rate limit.
6. **Вспомогательные скрипты**:
   - `backend/resync_ym.py` — ручной resync заказов (без finance, чтобы не ждать rate limit).
   - `backend/fetch_ym_finance.py` — выгрузка finance report за N дней и сохранение расходов в БД.

## Что нужно сделать

### 1. Дождаться и проверить finance report
- Запустить `backend/fetch_ym_finance.py 210` и дать ему **5 часов** (API ЯМ генерирует каждый чанк медленно, ~10+ минут на чанк + 120 сек между ними).
- После завершения проверить суммы:
  ```bash
  cd backend
  arch -x86_64 .venv/bin/python - <<'PY'
  import asyncio
  from sqlalchemy import text
  from app.database import get_async_session_maker
  shop_id = 'b87e8b29-f280-46f2-b25d-9055e9325401'
  async def check():
      async with get_async_session_maker()() as db:
          total = (await db.execute(text(
              "select sum(commission), sum(logistics), sum(storage), sum(advertising), "
              "sum(insurance), sum(acquiring), sum(other) from sales where shop_id=:shop_id"
          ), {"shop_id": shop_id})).fetchone()
          print(total)
  asyncio.run(check())
  PY
  ```
- Сверить с ЛК:
  - **Стоимость всех услуг Маркета без продвижения** = 670 585 ₽.
  - **Стоимость услуг продвижения** = 40 417 ₽.
- Если суммы не сходятся — править категоризацию в `backend/app/adapters/yandex_market.py::_download_and_parse_ym_report` (sheet_categories / service mapping).

### 2. Уточнить revenue / marketplace_discount
- Возможные причины расхождения в ~31 747 ₽:
  - `marketplace_discount` берёт `order.subsidies`, но в ЛК «Выручка» может включать не все субсидии (например, исключается YANDEX_CASHBACK или delivery subsidy).
  - Даты: мы фильтруем по `creationDate`, ЛК может считать по дате доставки.
  - 2 заказа расходятся по статусу (наши 1042 non-return vs ЛК 1042 delivered).
- Проверить, что `actual revenue` = `revenue + marketplace_discount` даёт 1 558 025 ₽. Если нет — попробовать:
  - `marketplace_discount` только item-level `subsidies`.
  - Исключить `YANDEX_CASHBACK` из `marketplace_discount`.
  - Использовать `itemsTotal` вместо суммы `buyerPrice * quantity` для revenue.

### 3. Сверить дашборд с ЛК
- После загрузки расходов открыть http://localhost:5173/static/ (если front не запущен — `cd frontend && npm run dev`).
- Проверить период «210 дней» (или 01.02.2026 – 31.08.2026) для ЯМ:
  - Выручка ≈ 1 558 025 ₽.
  - Заказы = 1042.
  - Средний чек ≈ 1 495 ₽.
  - Валовая прибыль, ДРР, маржа.
- Если нужно — поправить `backend/app/routers/dashboard.py` для ЯМ (`_gross_revenue`, `_actual_revenue`, expense aggregation).

### 4. Закоммитить и запушить
- `git add backend/app/adapters/yandex_market.py backend/app/services/sync_service.py backend/fetch_ym_finance.py backend/resync_ym.py PROMPT_YM_EXPENSES.md`
- `git commit` и `git push`.

## Ближайший шаг при возобновлении

1. **Запустить finance report**:
   ```bash
   cd /Users/peshkov/Yandex.Disk.localized/Development/locium_analitics/backend
   arch -x86_64 .venv/bin/python fetch_ym_finance.py 210 > /tmp/ym_finance.log 2>&1
   ```
2. **Ждать завершения** (следить за `/tmp/ym_finance.log`).
3. **Проверить расходы в БД и сверить с ЛК**.

## Полезные файлы

- `backend/app/adapters/yandex_market.py` — адаптер ЯМ (заказы + finance report).
- `backend/app/services/sync_service.py` — синхронизация, `_update_finance_data`.
- `backend/app/routers/dashboard.py` — расчёт дашборда.
- `backend/fetch_ym_finance.py` — скрипт выгрузки расходов.
- `backend/resync_ym.py` — скрипт выгрузки заказов.
