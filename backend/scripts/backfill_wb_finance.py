"""One-shot backfill of WB finance expenses for all historical sales.

The regular Celery sync only fetches finance data for the last 1-2 days
(days_back=1), so WB sales loaded for 90 days never received their
commission/logistics/storage/acquiring expenses. This script fetches ONE
finance report covering the full window (the endpoint paginates by rrdId
internally, so a single logical request is enough) and distributes the
expenses across the matching Sale rows via SyncService._update_finance_data.

WB finance-api is heavily rate-limited (Retry-After can be ~4h). The script
therefore respects Retry-After and keeps retrying until the request succeeds.

Usage:
    arch -x86_64 .venv/bin/python scripts/backfill_wb_finance.py [days]
"""

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(".env"))

from sqlalchemy import select  # noqa: E402

from app.adapters.wildberries import WildberriesAdapter  # noqa: E402
from app.database import get_async_session_maker  # noqa: E402
from app.models import Marketplace, Shop  # noqa: E402
from app.services.sync_service import SyncService  # noqa: E402
from app.utils.encryption import decrypt_dict  # noqa: E402


async def main() -> None:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    end = datetime.now()
    start = (end - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)

    async with get_async_session_maker()() as db:
        result = await db.execute(select(Shop).where(Shop.marketplace == Marketplace.wb))
        shop = result.scalar_one()
        credentials = decrypt_dict(shop.credentials)

        adapter = WildberriesAdapter(str(shop.id), credentials)
        service = SyncService(db)

        # Retry until the rate limiter lets us through (WB finance-api
        # Retry-After can be several hours; do not burn the quota with
        # short-cadence retries).
        finance = None
        for attempt in range(12):
            try:
                finance = await adapter.get_finance_report(start, end)
                break
            except Exception as e:
                msg = str(e)
                print(f"attempt {attempt + 1}: {msg}", flush=True)
                retry_after = 600
                if "retry after" in msg:
                    try:
                        retry_after = int(msg.split("retry after")[-1].strip().rstrip("s").strip())
                    except ValueError:
                        pass
                await asyncio.sleep(min(retry_after + 120, 14400))
        if finance is None:
            print("FAILED: finance report never succeeded", flush=True)
            return

        print(f"finance rows: {len(finance)} for {start.date()}..{end.date()}", flush=True)
        if not finance:
            print("nothing to distribute", flush=True)
            return

        await service._update_finance_data(shop.id, finance, start, end)
        await db.commit()
        print("distributed + committed", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
