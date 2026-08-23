"""Compare Ozon expenses from posting financial_data vs finance report."""
import asyncio
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.database import get_async_session_maker
from app.models import Shop, Marketplace
from app.adapters.ozon import OzonAdapter
from app.utils.encryption import decrypt_dict


def _sum_expenses(items):
    keys = ["commission", "logistics", "storage", "advertising", "returns", "insurance", "acquiring", "other"]
    result = {k: Decimal("0") for k in keys}
    for item in items:
        for k in keys:
            result[k] += Decimal(str(item.get(k, 0) or 0))
    return result


async def main():
    session_maker = get_async_session_maker()
    async with session_maker() as db:
        shop = (
            await db.execute(select(Shop).where(Shop.marketplace == Marketplace.ozon))
        ).scalars().first()

        if not shop:
            print("Ozon shop not found")
            return

        credentials = decrypt_dict(dict(shop.credentials or {}))
        adapter = OzonAdapter(str(shop.id), credentials)

        date_to = datetime.utcnow()
        date_from = (date_to - timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)

        print(f"Period: {date_from.date()} -> {date_to.date()}")

        orders = await adapter.get_orders(date_from, date_to)
        posting_expenses = _sum_expenses(orders)
        print("\n=== Posting financial_data expenses ===")
        for k, v in posting_expenses.items():
            print(f"  {k}: {v:,.2f}")
        print(f"  TOTAL: {sum(posting_expenses.values()):,.2f}")

        finance = await adapter.get_finance_report(date_from, date_to)
        finance_expenses = _sum_expenses(finance)
        print("\n=== Finance report expenses ===")
        for k, v in finance_expenses.items():
            print(f"  {k}: {v:,.2f}")
        print(f"  TOTAL: {sum(finance_expenses.values()):,.2f}")

        print("\n=== Combined (posting + finance) ===")
        combined = {k: posting_expenses[k] + finance_expenses[k] for k in posting_expenses}
        for k, v in combined.items():
            print(f"  {k}: {v:,.2f}")
        print(f"  TOTAL: {sum(combined.values()):,.2f}")


if __name__ == "__main__":
    asyncio.run(main())
