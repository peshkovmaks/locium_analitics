#!/usr/bin/env python3
"""Generate and poll Yandex Market united-marketplace-services report.

Wait for the 2-minute rate limit to cool down, then request a report by
accrual date range and poll until it is ready. Print the report file URL
and the number of parsed SKU expense records.
"""
import asyncio
import io
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.partner.market.yandex.ru"
API_KEY = os.getenv("YM_API_KEY", "").strip()
BUSINESS_ID = os.getenv("YM_BUSINESS_ID", "").strip()

HEADERS = {
    "Api-Key": API_KEY,
    "Content-Type": "application/json",
}

DATE_TO = datetime.now(timezone.utc)
DATE_FROM = DATE_TO - timedelta(days=7)


def _now() -> str:
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


def _read_sheet_names(z: zipfile.ZipFile) -> Dict[str, str]:
    names: Dict[str, str] = {}
    try:
        xml = z.read("xl/workbook.xml").decode("utf-8", errors="replace")
        root = ET.fromstring(xml)
        ns = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        for sheet in root.findall(".//main:sheet", ns):
            name = sheet.get("name", "")
            sheet_id = sheet.get("sheetId", "")
            if sheet_id:
                names[f"xl/worksheets/sheet{sheet_id}.xml"] = name
    except Exception as e:
        print(f"  Could not read workbook names: {e}")
    return names


def _parse_xlsx_sheet(z: zipfile.ZipFile, sheet_file: str) -> List[List[Any]]:
    try:
        xml = z.read(sheet_file).decode("utf-8", errors="replace")
    except Exception:
        return []
    ns = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []

    def col_index(col: str) -> int:
        idx = 0
        for ch in col:
            idx = idx * 26 + (ord(ch) - ord("A") + 1)
        return idx - 1

    rows: Dict[int, Dict[int, Any]] = {}
    for row in root.findall(".//main:row", ns):
        row_num = int(row.get("r", 0))
        cells: Dict[int, Any] = {}
        for c in row.findall("main:c", ns):
            ref = c.get("r", "")
            match = re.match(r"([A-Z]+)", ref)
            if not match:
                continue
            idx = col_index(match.group(1))
            cell_type = c.get("t", "")
            inline = c.find("main:is/main:t", ns)
            if inline is not None:
                value = inline.text or ""
            else:
                v = c.find("main:v", ns)
                value = ""
                if v is not None:
                    text = v.text or ""
                    if cell_type == "n":
                        try:
                            value = float(text)
                        except ValueError:
                            value = text
                    else:
                        value = text
            cells[idx] = value
        if cells:
            rows[row_num] = cells
    if not rows:
        return []
    max_row = max(rows.keys())
    max_col = max(max(cells.keys()) for cells in rows.values())
    return [
        [rows.get(r, {}).get(c, "") for c in range(max_col + 1)]
        for r in range(1, max_row + 1)
    ]


def _inspect_report(content: bytes) -> None:
    if not content.startswith(b"PK"):
        print("  Downloaded content is not an XLSX/ZIP archive")
        return
    print("  Archive is a ZIP/XLSX, listing sheets:")
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        names = _read_sheet_names(z)
        for sheet_file in z.namelist():
            if sheet_file.startswith("xl/worksheets/sheet") and sheet_file.endswith(".xml"):
                sheet_name = names.get(sheet_file, "unknown")
                rows = _parse_xlsx_sheet(z, sheet_file)
                print(f"    - {sheet_name}: {len(rows)} rows, first row: {rows[0] if rows else []}")


async def main():
    if not API_KEY or not BUSINESS_ID:
        print("YM_API_KEY and YM_BUSINESS_ID must be set")
        sys.exit(1)

    print(f"[{_now()}] Starting; rate-limit safety delay...")
    await asyncio.sleep(5)  # small safety buffer if called manually

    async with httpx.AsyncClient(headers=HEADERS) as client:
        print(f"[{_now()}] Requesting united-marketplace-services report...")
        gen = await _post(
            client,
            "/v2/reports/united-marketplace-services/generate",
            {
                "businessId": int(BUSINESS_ID),
                "dateFrom": DATE_FROM.strftime("%Y-%m-%d"),
                "dateTo": DATE_TO.strftime("%Y-%m-%d"),
            },
        )
        if "error" in gen:
            print(f"  Generation failed: {gen}")
            return

        report_id = (gen.get("result") or {}).get("reportId") or gen.get("reportId")
        if not report_id:
            print(f"  No reportId in response: {json.dumps(gen, ensure_ascii=False, default=str)[:500]}")
            return

        print(f"[{_now()}] reportId={report_id}, polling...")
        file_url = None
        for attempt in range(30):
            await asyncio.sleep(5)
            info = await _get(client, f"/v2/reports/info/{report_id}")
            if "error" in info:
                print(f"[{_now()}] poll {attempt+1}: error {info.get('status')} - {info.get('text')}")
                continue
            status = (info.get("result") or info or {}).get("status", "").upper()
            print(f"[{_now()}] poll {attempt+1}: status={status}")
            if status == "DONE":
                file_url = (info.get("result") or info or {}).get("file")
                break
            if status in ("FAILED", "ERROR", "CANCELLED"):
                print(f"  Report failed: {json.dumps(info, ensure_ascii=False, default=str)[:500]}")
                return

        if not file_url:
            print("  Report did not become ready in time")
            return

        print(f"[{_now()}] Downloading report from {file_url[:80]}...")
        async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as dl_client:
            resp = await dl_client.get(file_url)
            try:
                resp.raise_for_status()
                content = resp.content
                print(f"[{_now()}] Downloaded {len(content)} bytes")
                _inspect_report(content)
            except httpx.HTTPStatusError as e:
                print(f"  Download failed: {e} - {resp.text[:200]}")


if __name__ == "__main__":
    asyncio.run(main())
