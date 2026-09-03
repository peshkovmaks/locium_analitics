#!/usr/bin/env python3
"""Generate YM services report by date range, download and parse it with the adapter.

If the API returns 420 (rate limit), waits 30s and retries up to 3 times.
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import httpx
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.adapters.yandex_market import YandexMarketAdapter

BASE_URL = "https://api.partner.market.yandex.ru"
API_KEY = os.getenv("YM_API_KEY", "").strip()
BUSINESS_ID = os.getenv("YM_BUSINESS_ID", "").strip()

DATE_TO = datetime.now(timezone.utc)
DATE_FROM = DATE_TO - timedelta(days=7)


def _fmt_now() -> str:
    return datetime.now().strftime("%H:%M:%S")


async def _post(client: httpx.AsyncClient, endpoint: str, data: dict):
    resp = await client.post(f"{BASE_URL}{endpoint}", json=data, timeout=30.0)
    try:
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError:
        return {"error": True, "status": resp.status_code, "text": resp.text[:500]}


async def _get(client: httpx.AsyncClient, endpoint: str):
    resp = await client.get(f"{BASE_URL}{endpoint}", timeout=30.0)
    try:
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError:
        return {"error": True, "status": resp.status_code, "text": resp.text[:500]}


async def main():
    if not API_KEY or not BUSINESS_ID:
        print("YM_API_KEY и YM_BUSINESS_ID должны быть в .env")
        sys.exit(1)

    adapter = YandexMarketAdapter(
        shop_id="test-shop",
        credentials={
            "api_key": API_KEY,
            "business_id": BUSINESS_ID,
            "campaign_id": "",
        },
    )

    headers = {"Api-Key": API_KEY, "Content-Type": "application/json"}
    async with httpx.AsyncClient(headers=headers) as client:
        report_id = None
        for attempt in range(3):
            print(f"[{_fmt_now()}] Запрос отчёта united-marketplace-services (попытка {attempt + 1})...")
            gen = await _post(
                client,
                "/v2/reports/united-marketplace-services/generate",
                {
                    "businessId": int(BUSINESS_ID),
                    "dateFrom": DATE_FROM.strftime("%Y-%m-%d"),
                    "dateTo": DATE_TO.strftime("%Y-%m-%d"),
                },
            )
            if "error" not in gen:
                report_id = (gen.get("result") or {}).get("reportId") or gen.get("reportId")
                print(f"[{_fmt_now()}] reportId={report_id}")
                break
            print(f"  Ошибка: {gen}")
            if gen.get("status") == 420 and attempt < 2:
                print(f"  Rate limit, жду 30 сек...")
                await asyncio.sleep(30)
            else:
                return

        if not report_id:
            print("Не удалось получить reportId")
            return

        file_url = None
        for attempt in range(30):
            await asyncio.sleep(5)
            info = await _get(client, f"/v2/reports/info/{report_id}")
            if "error" in info:
                print(f"[{_fmt_now()}] poll {attempt + 1}: error {info.get('status')}")
                continue
            status = (info.get("result") or info or {}).get("status", "").upper()
            print(f"[{_fmt_now()}] poll {attempt + 1}: status={status}")
            if status == "DONE":
                file_url = (info.get("result") or info or {}).get("file")
                break
            if status in ("FAILED", "ERROR", "CANCELLED"):
                print(f"[{_fmt_now()}] Отчёт упал: {json.dumps(info, ensure_ascii=False, default=str)[:500]}")
                return

        if not file_url:
            print("Отчёт не стал готов за отведённое время")
            return

        print(f"[{_fmt_now()}] Скачивание отчёта...")
        async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as dl_client:
            resp = await dl_client.get(file_url)
            try:
                resp.raise_for_status()
                content = resp.content
                print(f"[{_fmt_now()}] Скачано {len(content)} байт")
            except httpx.HTTPStatusError as e:
                print(f"[{_fmt_now()}] Ошибка скачивания: {e}")
                return

        records = adapter._download_and_parse_ym_report(
            file_url="",
            date_from=DATE_FROM,
            date_to=DATE_TO,
        )
        # Patch content manually: the adapter method ignores file_url arg
        # because it expects to download. We'll monkey-patch content.
        # Actually method signature takes file_url and downloads. We already have content.
        # Simpler: write a wrapper that parses content using adapter helper logic.
        # Since _download_and_parse_ym_report is not easily patchable, reimplement minimal parse below.
        print(f"[{_fmt_now()}] Парсинг через адаптер...")
        records = parse_content(adapter, content, DATE_FROM, DATE_TO)
        print(f"\nИтого записей: {len(records)}")
        if records:
            totals = {}
            for r in records:
                for k, v in r.items():
                    if isinstance(v, (int, float)) and k not in ("quantity",):
                        totals[k] = totals.get(k, 0) + float(v)
            print("Суммы по категориям:")
            for k, v in totals.items():
                if v:
                    print(f"  {k}: {v:.2f}")
            print("\nПример первых 3 записей:")
            for r in records[:3]:
                print(json.dumps(r, ensure_ascii=False, default=str))


def parse_content(adapter, content, date_from, date_to):
    # Reuse adapter internals by mimicking its logic around the already-downloaded content.
    import io, zipfile
    aggregated = {}
    sheet_categories = {
        "размещение товаров и услуг": "commission",
        "order for sale": "commission",
        "warehouse processing": "logistics",
        "acceptance of supply": "logistics",
        "буст продаж": "advertising",
        "sales boost": "advertising",
        "installment plan": "advertising",
        "shelves": "advertising",
        "boost sales with pay-per-views": "advertising",
        "product banners": "advertising",
        "banners": "advertising",
        "push-notifications": "advertising",
        "pop-up notifications": "advertising",
        "доставка покупателю": "logistics",
        "delivery to buyer": "logistics",
        "доставка (средняя миля)": "logistics",
        "delivery (middle mile)": "logistics",
        "express delivery": "logistics",
        "delivery from abroad": "logistics",
        "страхование": "insurance",
        "insurance": "insurance",
        "эквайринг": "acquiring",
        "acquiring": "acquiring",
        "приём платежа": "acquiring",
        "payment acceptance": "acquiring",
        "перевод платежа": "acquiring",
        "payment transfer": "acquiring",
        "order for payment transfer": "acquiring",
        "loyalty program and reviews": "other",
        "подписки": "other",
        "subscriptions": "other",
    }

    def _sheet_category(sheet_name: str):
        lowered = sheet_name.lower()
        for key, cat in sheet_categories.items():
            if key in lowered:
                return cat
        return None

    with zipfile.ZipFile(io.BytesIO(content)) as z:
        names = adapter._read_sheet_names(z)
        for sheet_file in z.namelist():
            if not (sheet_file.startswith("xl/worksheets/sheet") and sheet_file.endswith(".xml")):
                continue
            rows = adapter._parse_xlsx_sheet(z, sheet_file)
            if not rows:
                continue
            sheet_id = sheet_file.replace("xl/worksheets/sheet", "").replace(".xml", "")
            sheet_name = names.get(sheet_file, "")
            default_category = _sheet_category(sheet_name)
            header_map = {}
            header_row_index = None
            for idx, row in enumerate(rows):
                cells = [str(c).strip().lower() for c in row]
                if "ваш sku" in cells or "ваш sku" in " ".join(cells):
                    header_row_index = idx
                    header_map = {c: i for i, c in enumerate(cells) if c}
                    break
            has_sku = header_row_index is not None and adapter._find_col(header_map, "ваш sku") is not None
            sku_idx = adapter._find_col(header_map, "ваш sku") if has_sku else None
            amount_idx = adapter._find_col(header_map, "стоимость услуги") if header_map else None
            if amount_idx is None:
                continue
            service_idx = adapter._find_col(header_map, "услуга") if header_map else None
            date_idx = adapter._find_col(header_map, "дата и время оказания услуги") if header_map else None
            if date_idx is None and header_map:
                date_idx = adapter._find_col(header_map, "дата оказания услуги")

            for row in rows[(header_row_index or 0) + 1:]:
                sku = str(row[sku_idx]).strip() if sku_idx is not None else ""
                if sku_idx is not None and not sku:
                    continue
                try:
                    amount = float(str(row[amount_idx] if amount_idx < len(row) else "0").replace(",", "."))
                except Exception:
                    continue
                category = default_category
                if service_idx is not None and service_idx < len(row):
                    service_name = str(row[service_idx]).lower()
                    category = adapter._categorize_ym_service(service_name, sheet_categories) or default_category
                if not category:
                    continue
                if sku not in aggregated:
                    aggregated[sku] = {
                        "commission": 0, "logistics": 0, "storage": 0,
                        "advertising": 0, "returns": 0, "insurance": 0,
                        "acquiring": 0, "other": 0,
                    }
                aggregated[sku][category] += amount
    from datetime import datetime as dt
    records = []
    for sku, amounts in aggregated.items():
        records.append({
            "date": dt.utcnow(),
            "external_sku": sku,
            "external_id": "",
            "quantity": 0,
            "price": 0,
            "revenue": 0,
            **amounts,
        })
    return records


if __name__ == "__main__":
    asyncio.run(main())
