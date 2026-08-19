# Инструкции для AI-агентов

## Архитектура

- Основной backend находится в `backend/`: FastAPI роутеры -> `SyncService` -> `AdapterFactory`/адаптер маркетплейса -> PostgreSQL через async SQLAlchemy.
- Реализации API маркетплейсов находятся в `backend/app/adapters/`; общий контракт задан в `adapters/base.py`. Добавляя метод адаптера, проверяйте его вызов в `services/sync_service.py` и формат записи в `models.py`.
- Магазин хранит credentials в зашифрованном JSONB. Расшифровка выполняется в `routers/shops.py` перед ручной синхронизацией; не логируйте ключи и не записывайте расшифрованные credentials в ORM.
- Синхронизация обрабатывает секции независимо: orders/sales, stocks, adverts, prices и finance. Ошибка одной секции попадает в `SyncLog.sections`, но не должна скрывать результаты остальных секций.
- Ручной sync: `POST /api/v1/shops/{shop_id}/sync`. Фоновый sync запускается Celery Beat каждые 4 часа; `tasks/sync.py` создаёт новый async engine на каждый `asyncio.run`, это важно для исправления loop-related ошибок.
- Данные дашборда читает `routers/dashboard.py`; не меняйте внешний формат адаптера без проверки преобразований и SQL-запросов дашборда.

## Wildberries: обязательная диагностика

- WB использует разные base URL: statistics, marketplace, advert и analytics; они перечислены в `adapters/wildberries.py`. Один API-токен должен иметь необходимые категории доступа.
- Статистика WB пагинируется по `lastChangeDate`, а не по `dateTo`; не удаляйте эту логику, проверяя границы и дубликаты.
- В `wildberries.py` комментарий указывает на отключение `/api/v1/supplier/stocks` и переход к асинхронному Analytics warehouse-remains report, но текущий `get_stocks()` всё ещё вызывает старый endpoint. Сначала подтвердите реальный HTTP status/body и актуальную документацию WB, затем исправляйте адаптер и тест.
- Для проверки credentials и отдельных endpoint используйте корневой `test_wb.py`; он сохраняет `wb_test_results_*.json`. Не коммитьте реальные API-ключи или результаты с чувствительными данными.
- Учитывайте лимиты: statistics throttled примерно на 70 секунд, retry использует `app/utils/retry.py`; не добавляйте агрессивный polling или параллелизм без проверки rate limits.

## Запуск и проверки

- Backend локально: `cd backend && docker-compose up --build`; API доступен на `http://localhost:8000`, Swagger на `/docs`, health check на `/health`.
- Миграции Alembic: запускать из `backend/`, например `alembic upgrade head`; startup FastAPI также вызывает `Base.metadata.create_all`, поэтому изменения схемы нужно согласовывать с миграциями.
- Backend tests: `cd backend && python -m pytest tests -v`. Сейчас в репозитории есть покрытие адаптера Ozon; для изменений WB добавляйте mock-тесты HTTP-ответов рядом с ним.
- Frontend: `cd frontend && npm install && npm run dev`; перед изменениями запускайте `npm run build` и при необходимости `npm run lint`.
- Docker Compose поднимает PostgreSQL, Redis, API, Celery worker и Celery beat. Переменные берутся из `backend/.env`; README упоминает `.env.example`, но наличие файла проверяйте отдельно.

## Соглашения проекта

- Адаптеры асинхронные и возвращают нормализованные словари с `external_sku`, `external_id`, датами и `Decimal`; преобразование marketplace-specific полей держите внутри адаптера.
- Сохранение продаж использует PostgreSQL upsert по `(shop_id, external_id, external_sku)`, stocks сначала очищаются для магазина, adverts очищаются за период. Сохраняйте эту идемпотентность при изменениях.
- Даты API могут быть timezone-aware, а БД хранит naive datetime; используйте существующий `_naive_dt()` в `SyncService` и не смешивайте часовые пояса неявно.
- Секреты, токены и `.env` не должны попадать в логи, тестовые фикстуры, коммиты или ответы API.