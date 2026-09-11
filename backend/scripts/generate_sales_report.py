"""Generate a monthly sales report PDF from the local analytics database."""

import asyncio
import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
)
from sqlalchemy import select

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.database import get_async_session_maker  # noqa: E402
from app.models import FinanceTransaction, Product, Sale, Shop, User  # noqa: E402
from app.services.metrics import (
    buyer_revenue,
    gross_revenue,
    sale_expenses,
)  # noqa: E402


def money(value: Decimal) -> str:
    return f"{value:,.0f} ₽".replace(",", " ")


def number(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def register_font() -> str:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            pdfmetrics.registerFont(TTFont("ReportFont", path))
            return "ReportFont"
    raise RuntimeError("A Cyrillic-capable font was not found")


async def load_data():
    start = datetime(2026, 8, 1)
    end = datetime(2026, 9, 1)
    async with get_async_session_maker()() as db:
        user = (
            (await db.execute(select(User).order_by(User.created_at))).scalars().first()
        )
        if not user:
            raise RuntimeError("No users found")
        shops = (
            (await db.execute(select(Shop).where(Shop.user_id == user.id)))
            .scalars()
            .all()
        )
        shop_ids = [shop.id for shop in shops]
        sales = (
            (
                await db.execute(
                    select(Sale).where(
                        Sale.shop_id.in_(shop_ids), Sale.date >= start, Sale.date < end
                    )
                )
            )
            .scalars()
            .all()
            if shop_ids
            else []
        )
        products = {
            product.sku: product
            for product in (
                await db.execute(select(Product).where(Product.user_id == user.id))
            )
            .scalars()
            .all()
        }
        ozon_ids = [shop.id for shop in shops if shop.marketplace.value == "ozon"]
        finance = (
            (
                await db.execute(
                    select(FinanceTransaction).where(
                        FinanceTransaction.shop_id.in_(ozon_ids),
                        FinanceTransaction.operation_date >= start,
                        FinanceTransaction.operation_date < end,
                    )
                )
            )
            .scalars()
            .all()
            if ozon_ids
            else []
        )
    return start, user, shops, sales, products, finance


def build_pdf(path: Path, start, user, shops, sales, products, finance):
    font = register_font()
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="ReportTitle",
            parent=styles["Title"],
            fontName=font,
            fontSize=22,
            leading=27,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#17324D"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="Heading",
            parent=styles["Heading2"],
            fontName=font,
            fontSize=14,
            leading=18,
            textColor=colors.HexColor("#17324D"),
            spaceBefore=10,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Body",
            parent=styles["BodyText"],
            fontName=font,
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#263238"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="Small",
            parent=styles["BodyText"],
            fontName=font,
            fontSize=7.5,
            leading=9,
            textColor=colors.HexColor("#455A64"),
        )
    )

    regular = [sale for sale in sales if not sale.is_return]
    returns = [sale for sale in sales if sale.is_return]
    ozon_expenses = defaultdict(Decimal)
    for transaction in finance:
        ozon_expenses[transaction.shop_id] += Decimal(str(transaction.amount or 0))

    revenue = sum((gross_revenue(sale) for sale in regular), Decimal(0))
    returned_revenue = sum((gross_revenue(sale) for sale in returns), Decimal(0))
    net_revenue = revenue - returned_revenue
    actual = sum((buyer_revenue(sale) for sale in regular), Decimal(0))
    actual -= sum((buyer_revenue(sale) for sale in returns), Decimal(0))
    expenses = Decimal(0)
    cost = Decimal(0)
    for shop in shops:
        shop_sales = [sale for sale in regular if sale.shop_id == shop.id]
        shop_returns = [sale for sale in returns if sale.shop_id == shop.id]
        if shop.marketplace.value == "ozon":
            expenses += ozon_expenses[shop.id]
        else:
            expenses += sum(
                (sale_expenses(sale) for sale in shop_sales + shop_returns), Decimal(0)
            )
        cost += sum(
            (
                Decimal(str(products[sale.external_sku].cost_price or 0))
                * (sale.quantity or 0)
                for sale in shop_sales
                if sale.external_sku in products
            ),
            Decimal(0),
        )
    gross = net_revenue - expenses
    net = gross - cost

    story = [
        Paragraph("Отчёт о продажах", styles["ReportTitle"]),
        Spacer(1, 4 * mm),
        Paragraph("Август 2026 · 01.08.2026–31.08.2026", styles["Body"]),
        Paragraph(f"Пользователь: {user.email}", styles["Small"]),
        Spacer(1, 8 * mm),
    ]

    kpis = [
        [
            Paragraph("Показатель", styles["Body"]),
            Paragraph("Значение", styles["Body"]),
        ],
        ["Заказы", number(len({sale.external_id for sale in regular}))],
        ["Продано, шт.", number(sum(sale.quantity or 0 for sale in regular))],
        ["Выручка до возвратов", money(revenue)],
        [
            "Возвраты",
            f"{number(len({sale.external_id for sale in returns}))} · {money(returned_revenue)}",
        ],
        ["Выручка после возвратов", money(net_revenue)],
        ["Фактическая выручка", money(actual)],
        ["Расходы", money(expenses)],
        ["Себестоимость", money(cost)],
        ["Прибыль", money(net)],
        ["Маржинальность", f"{(net / net_revenue * 100 if net_revenue else 0):.1f}%"],
    ]
    story.append(
        Table(
            kpis,
            colWidths=[75 * mm, 95 * mm],
            style=TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), font),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCEAF4")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#17324D")),
                    ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#B0BEC5")),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, colors.HexColor("#F5F8FA")],
                    ),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ]
            ),
        )
    )

    story.append(Paragraph("Продажи по маркетплейсам", styles["Heading"]))
    marketplace_rows = [["Площадка", "Заказы", "Шт.", "Выручка", "Расходы", "Прибыль"]]
    for shop in shops:
        shop_sales = [sale for sale in regular if sale.shop_id == shop.id]
        shop_returns = [sale for sale in returns if sale.shop_id == shop.id]
        shop_revenue = sum(
            (gross_revenue(sale) for sale in shop_sales), Decimal(0)
        ) - sum((gross_revenue(sale) for sale in shop_returns), Decimal(0))
        shop_expenses = (
            ozon_expenses[shop.id]
            if shop.marketplace.value == "ozon"
            else sum(
                (sale_expenses(sale) for sale in shop_sales + shop_returns), Decimal(0)
            )
        )
        shop_cost = sum(
            (
                Decimal(str(products[sale.external_sku].cost_price or 0))
                * (sale.quantity or 0)
                for sale in shop_sales
                if sale.external_sku in products
            ),
            Decimal(0),
        )
        marketplace_rows.append(
            [
                f"{shop.marketplace.value.upper()} · {shop.name}",
                number(len({sale.external_id for sale in shop_sales})),
                number(sum(sale.quantity or 0 for sale in shop_sales)),
                money(shop_revenue),
                money(shop_expenses),
                money(shop_revenue - shop_expenses - shop_cost),
            ]
        )
    story.append(
        Table(
            marketplace_rows,
            colWidths=[42 * mm, 22 * mm, 18 * mm, 28 * mm, 28 * mm, 28 * mm],
            style=TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), font),
                    ("FONTSIZE", (0, 0), (-1, -1), 7),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCEAF4")),
                    ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#B0BEC5")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, colors.HexColor("#F5F8FA")],
                    ),
                ]
            ),
        )
    )

    product_totals = defaultdict(
        lambda: {"name": "", "units": 0, "revenue": Decimal(0)}
    )
    for sale in regular:
        product = products.get(sale.external_sku)
        key = sale.external_sku
        product_totals[key]["name"] = product.name if product else key
        product_totals[key]["units"] += sale.quantity or 0
        product_totals[key]["revenue"] += gross_revenue(sale)
    top_products = sorted(
        product_totals.values(), key=lambda item: item["revenue"], reverse=True
    )[:20]
    story.append(PageBreak())
    story.append(Paragraph("Топ товаров", styles["Heading"]))
    product_rows = [["#", "Товар", "Шт.", "Выручка"]]
    for index, item in enumerate(top_products, 1):
        product_rows.append(
            [
                str(index),
                item["name"][:70],
                number(item["units"]),
                money(item["revenue"]),
            ]
        )
    story.append(
        Table(
            product_rows,
            colWidths=[10 * mm, 105 * mm, 25 * mm, 35 * mm],
            style=TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), font),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCEAF4")),
                    ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#B0BEC5")),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, colors.HexColor("#F5F8FA")],
                    ),
                ]
            ),
        )
    )

    daily = defaultdict(lambda: {"orders": set(), "revenue": Decimal(0), "units": 0})
    for sale in regular:
        day = sale.date.strftime("%d.%m")
        daily[day]["orders"].add(sale.external_id)
        daily[day]["revenue"] += gross_revenue(sale)
        daily[day]["units"] += sale.quantity or 0
    story.append(Paragraph("Динамика по дням", styles["Heading"]))
    daily_rows = [["Дата", "Заказы", "Шт.", "Выручка"]]
    for day in sorted(daily, key=lambda value: datetime.strptime(value, "%d.%m")):
        item = daily[day]
        daily_rows.append(
            [
                day,
                number(len(item["orders"])),
                number(item["units"]),
                money(item["revenue"]),
            ]
        )
    story.append(
        Table(
            daily_rows,
            colWidths=[35 * mm, 35 * mm, 35 * mm, 70 * mm],
            repeatRows=1,
            style=TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), font),
                    ("FONTSIZE", (0, 0), (-1, -1), 7),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCEAF4")),
                    ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#B0BEC5")),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, colors.HexColor("#F5F8FA")],
                    ),
                ]
            ),
        )
    )

    story.append(Spacer(1, 6 * mm))
    story.append(
        Paragraph(
            "Примечание: расходы Ozon рассчитаны по финансовым транзакциям за дату операции; "
            "расходы WB и Яндекс Маркета — по расходам в строках продаж. Себестоимость учитывает "
            "только проданные товары без вычета возвратов, в соответствии с логикой дашборда.",
            styles["Small"],
        )
    )
    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title="Отчёт о продажах за август 2026",
        author="Locium Analytics",
    )
    doc.build(story)


async def main():
    data = await load_data()
    output = ROOT / "reports" / "sales_report_2026-08.pdf"
    output.parent.mkdir(exist_ok=True)
    build_pdf(output, *data)
    print(output)


if __name__ == "__main__":
    asyncio.run(main())
