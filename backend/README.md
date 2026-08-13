# Multi-Marketplace Analytics Dashboard

Аналитический дашборд для продавцов на Wildberries, Ozon и Яндекс Маркет.

## Архитектура

```
Frontend (React 19 + Tailwind + Recharts)
    ↕
Backend API (FastAPI + PostgreSQL + Redis)
    ↕
Marketplace Adapters (WB / Ozon / YM APIs)
    ↕
Celery Workers (синхронизация каждые 4 часа)
    ↕
Telegram Bot (ежевечерний отчёт + алерты)
```

## Стек

- **Backend**: Python 3.12, FastAPI, SQLAlchemy 2.0 (async), PostgreSQL 16, Redis 7, Celery
- **Frontend**: React 19, TypeScript, Tailwind CSS, Recharts
- **APIs**: Wildberries API, Ozon Seller API, Yandex Market Partner API
- **Auth**: JWT + роли (admin / viewer)
- **Security**: Fernet-шифрование API-ключей

## Быстрый старт

### 1. Клонирование и настройка

```bash
cd backend
cp .env.example .env
# Отредактируй .env — укажи свои ключи
```

### 2. Запуск через Docker

```bash
docker-compose up --build
```

Сервисы:
- API: http://localhost:8000
- Документация API: http://localhost:8000/docs
- PostgreSQL: localhost:5432
- Redis: localhost:6379

### 3. Первая настройка

```bash
# Регистрация пользователя (admin)
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@example.com", "password": "password123", "role": "admin"}'

# Логин
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@example.com", "password": "password123"}'
```

### 4. Добавление магазинов

```bash
# Wildberries
curl -X POST http://localhost:8000/api/v1/shops/shops \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "marketplace": "wb",
    "name": "Мой WB магазин",
    "credentials": {"api_key": "YOUR_WB_API_KEY"}
  }'

# Ozon
curl -X POST http://localhost:8000/api/v1/shops/shops \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "marketplace": "ozon",
    "name": "Мой Ozon магазин",
    "credentials": {"client_id": "YOUR_CLIENT_ID", "api_key": "YOUR_API_KEY"}
  }'

# Яндекс Маркет
curl -X POST http://localhost:8000/api/v1/shops/shops \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "marketplace": "ym",
    "name": "Мой ЯМ магазин",
    "credentials": {
      "oauth_token": "YOUR_OAUTH_TOKEN",
      "business_id": "YOUR_BUSINESS_ID",
      "campaign_id": "YOUR_CAMPAIGN_ID"
    }
  }'
```

### 5. Добавление товаров в каталог

```bash
curl -X POST http://localhost:8000/api/v1/catalog/products \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "sku": "SKU-001",
    "name": "Кружка керамическая",
    "cost_price": 120.00,
    "min_price": 450.00,
    "category": "Посуда"
  }'
```

### 6. Ручная синхронизация

```bash
curl -X POST http://localhost:8000/api/v1/shops/shops/{shop_id}/sync \
  -H "Authorization: Bearer <TOKEN>"
```

### 7. Получение данных дашборда

```bash
curl "http://localhost:8000/api/v1/dashboard/data?period=today&marketplace=all" \
  -H "Authorization: Bearer <TOKEN>"
```

## API Endpoints

### Auth
- `POST /api/v1/auth/register` — регистрация
- `POST /api/v1/auth/login` — логин (JWT)
- `GET /api/v1/auth/me` — текущий пользователь

### Catalog (только admin)
- `GET /api/v1/catalog/products` — список товаров
- `POST /api/v1/catalog/products` — добавить товар
- `PUT /api/v1/catalog/products/{id}` — обновить товар
- `DELETE /api/v1/catalog/products/{id}` — удалить товар

### Shops (только admin)
- `GET /api/v1/shops/shops` — список магазинов
- `POST /api/v1/shops/shops` — добавить магазин
- `PUT /api/v1/shops/shops/{id}/toggle-sync` — вкл/выкл синхронизацию
- `POST /api/v1/shops/shops/{id}/sync` — ручная синхронизация

### Dashboard (admin + viewer)
- `GET /api/v1/dashboard/data?period=today&marketplace=all` — данные дашборда

## Структура проекта

```
backend/
├── app/
│   ├── adapters/           # Адаптеры маркетплейсов
│   │   ├── base.py         # Базовый класс + фабрика
│   │   ├── wildberries.py  # WB API
│   │   ├── ozon.py         # Ozon API
│   │   └── yandex_market.py # YM API
│   ├── routers/            # API endpoints
│   │   ├── auth.py         # Авторизация
│   │   ├── catalog.py      # Каталог товаров
│   │   ├── shops.py        # Управление магазинами
│   │   └── dashboard.py    # Дашборд
│   ├── services/           # Бизнес-логика
│   │   ├── sync_service.py # Синхронизация данных
│   │   └── telegram_bot.py # Telegram-уведомления
│   ├── tasks/              # Celery задачи
│   │   └── sync.py         # Фоновые задачи
│   ├── utils/              # Утилиты
│   │   └── encryption.py   # Шифрование credentials
│   ├── models.py           # SQLAlchemy модели
│   ├── schemas.py          # Pydantic схемы
│   ├── database.py         # Подключение к БД
│   ├── auth.py             # JWT + хеширование
│   ├── config.py           # Настройки
│   ├── celery_app.py       # Celery конфигурация
│   └── main.py             # Точка входа
├── alembic/                # Миграции БД
├── docker-compose.yml      # Docker оркестрация
├── Dockerfile              # Сборка контейнера
├── requirements.txt        # Зависимости
└── .env.example            # Шаблон переменных
```

## Модели БД

### users
- id, email, password_hash, role (admin/viewer), created_at

### shops
- id, user_id, marketplace (wb/ozon/ym), name, credentials (encrypted JSONB), is_active, sync_enabled, last_sync_at

### products
- id, user_id, sku, name, cost_price, min_price, weight_kg, category

### shop_products
- id, shop_id, product_id, external_sku, external_id, is_active

### sales
- id, shop_id, date, external_sku, quantity, price, revenue, commission, logistics, storage, advertising, returns, other, is_return

### stocks
- id, shop_id, date, external_sku, warehouse, quantity, in_way

### adverts
- id, shop_id, date, campaign_id, external_sku, views, clicks, ctr, cpc, spend, orders, cr

## Фоновые задачи (Celery)

| Задача | Расписание | Описание |
|--------|-----------|----------|
| sync_all_shops_task | Каждые 4 часа | Синхронизация всех активных магазинов |
| send_daily_report_task | 21:00 ежедневно | Telegram-отчёт за день |
| check_alerts_task | Каждый час | Проверка алертов (цена, остаток, ДРР) |

## Алерты

- 🔴 **Цена ниже минимальной** — если цена на витрине < min_price из каталога
- 🟡 **Низкий остаток** — если остаток < 10 шт
- 🔴 **Высокий ДРР** — если ДРР > 12%
- 🟡 **Низкая маржа** — если чистая маржа < 15%

## Формулы

```
Валовая прибыль = Выручка − (Комиссия + Логистика + Хранение + Реклама + Возвраты + Прочее)
Чистая прибыль = Валовая − (Себестоимость × Количество)
ДРР = Расход на рекламу / Выручка × 100%
Чистая маржа = Чистая прибыль / Выручка × 100%
```

## Безопасность

- API-ключи маркетплейсов шифруются через Fernet (AES-128)
- JWT-токены с ограниченным сроком жизни (60 мин)
- Ролевая модель: Admin (полный доступ) / Viewer (только просмотр)
- CORS настроен для разработки (изменить в production)

## Лицензия

MIT
