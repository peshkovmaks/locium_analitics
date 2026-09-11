"""Shared per-sale metric helpers used by the dashboard and Telegram reports.

Single source of truth for revenue/expense semantics:

- ``gross_revenue`` — "Выручка": money the seller receives, including
  marketplace discount/СПП/bonus compensation.
- ``buyer_revenue`` / ``actual_revenue`` — "Фактическая выручка": amount
  actually paid by the customer.
- ``sale_expenses`` — sum of all per-sale expense columns (commission,
  logistics, storage, advertising, returns, insurance, acquiring, other).
"""

from decimal import Decimal

from app.models import Sale


def to_decimal(value) -> Decimal:
    return Decimal(str(value or 0))


def sale_expenses(s: Sale) -> Decimal:
    return (
        max(to_decimal(s.commission), Decimal(0))
        + max(to_decimal(s.logistics), Decimal(0))
        + max(to_decimal(s.storage), Decimal(0))
        + max(to_decimal(s.advertising), Decimal(0))
        + max(to_decimal(s.returns), Decimal(0))
        + max(to_decimal(s.insurance), Decimal(0))
        + max(to_decimal(s.acquiring), Decimal(0))
        + max(to_decimal(s.other), Decimal(0))
    )


def gross_revenue(s: Sale) -> Decimal:
    """Order amount including marketplace discount/СПП/bonus compensation —
    the money the seller actually receives."""
    cp = to_decimal(s.customer_price)
    if cp > 0:
        return min(to_decimal(s.price), cp) * (s.quantity or 0) + to_decimal(
            s.marketplace_discount
        )
    return to_decimal(s.revenue) + to_decimal(s.marketplace_discount)


def buyer_revenue(s: Sale) -> Decimal:
    """Amount actually paid by the customer."""
    cp = to_decimal(s.customer_price)
    if cp > 0:
        return cp * (s.quantity or 0)
    return to_decimal(s.revenue)


def actual_revenue(s: Sale) -> Decimal:
    """Actually paid by the customer (alias of buyer_revenue)."""
    return buyer_revenue(s)


def signed_finance_amount(transaction) -> Decimal:
    """Return a finance transaction using the current signed convention.

    Legacy rows stored expenses as positive amounts. New Ozon accrual rows carry
    ``raw_data.signed`` and preserve the API sign, including positive credits.
    """
    amount = to_decimal(transaction.amount)
    if (transaction.raw_data or {}).get("signed"):
        return amount
    return -abs(amount)
