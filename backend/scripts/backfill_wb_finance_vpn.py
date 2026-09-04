"""WB finance expenses backfill — run manually with VPN enabled.

Fetches ONE detailed sales report from WB finance-api covering the last 90
days and distributes commission/logistics/storage/acquiring expenses across
the matching Sale rows in the local DB.

The script is intentionally conservative: at most 3 attempts, ≥90s apart,
because WB finance-api blocks the caller IP for hours on abuse.

Run (from the backend directory, with VPN on):
    arch -x86_64 .venv/bin/python scripts/backfill_wb_finance_vpn.py
"""

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from sqlalchemy import select  # noqa: E402

from app.adapters.wildberries import WildberriesAdapter  # noqa: E402
from app.database import get_async_session_maker  # noqa: E402
from app.models import Marketplace, Shop  # noqa: E402
from app.services.sync_service import SyncService  # noqa: E402
from app.utils.encryption import decrypt_dict  # noqa: E402


async def main() -> int:
    days = 90
    end = datetime.now()
    start = (end - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    print(f"period: {start.date()} .. {end.date()}")

    async with get_async_session_maker()() as db:
        result = await db.execute(select(Shop).where(Shop.marketplace == Marketplace.wb))
        shop = result.scalar_one()
        credentials = decrypt_dict(shop.credentials)
        adapter = WildberriesAdapter(str(shop.id), credentials)
        service = SyncService(db)

        finance = None
        for attempt in range(1, 4):
            try:
                finance = await adapter.get_finance_report(start, end)
                break
            except Exception as e:
                print(f"attempt {attempt}/3 failed: {type(e).__name__}: {e}", flush=True)
                if attempt < 3:
                    await asyncio.sleep(90)
        if finance is None:
            print("FAILED: could not reach WB finance-api. Is the VPN on?")
            return 1

        print(f"OK: {len(finance)} finance rows fetched")
        if not finance:
            print("nothing to distribute")
            return 0

        await service._update_finance_data(shop.id, finance, start, end)
        await db.commit()
        print("distributed + committed — backfill complete")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
