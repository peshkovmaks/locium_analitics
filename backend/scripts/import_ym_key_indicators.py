"""Process a locally saved YM key-indicators XLSX into Sale expense rows.

The seller-cabinet export covers a longer history than the API allows
(Medium tariff caps the API at ~90 days). This script parses the local file
with the same parser used for API downloads and distributes monthly expenses
across the YM sales in each period.

Usage:
    arch -x86_64 .venv/bin/python scripts/import_ym_key_indicators.py /path/to/file.xlsx
"""

import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from sqlalchemy import select  # noqa: E402

from app.adapters.yandex_market import YandexMarketAdapter  # noqa: E402
from app.database import get_async_session_maker  # noqa: E402
from app.models import Marketplace, Shop  # noqa: E402
from app.services.sync_service import SyncService  # noqa: E402


async def main() -> None:
    if len(sys.argv) < 2:
        print("usage: import_ym_key_indicators.py <file.xlsx>")
        return
    path = Path(sys.argv[1])
    content = path.read_bytes()

    async with get_async_session_maker()() as db:
        result = await db.execute(select(Shop).where(Shop.marketplace == Marketplace.yandex_market))
        shop = result.scalar_one()

        adapter = YandexMarketAdapter(str(shop.id), {"api_key": "dummy"})
        # Wide range so the parser keeps every period row in the file.
        date_from = datetime(2020, 1, 1)
        date_to = datetime(2030, 12, 31)
        reports = adapter._parse_key_indicators_xlsx(content, date_from, date_to)
        print(f"parsed {len(reports)} period rows", flush=True)
        for r in reports:
            print(
                f"  {r['date_from'].date()}..{r['date_to'].date()} "
                f"commission={r['commission']} logistics={r['logistics']} "
                f"storage={r['storage']} advertising={r['advertising']} "
                f"acquiring={r['acquiring']} other={r['other']}",
                flush=True,
            )
        if not reports:
            print("nothing to import")
            return

        service = SyncService(db)
        await service._update_finance_data_by_period(shop.id, reports, date_from, date_to)
        await db.commit()
        print("distributed + committed", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
