"""Configurable sales report preview and PDF export."""

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from pathlib import Path
from typing import Any
import zipfile
import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.graphics.shapes import Drawing, Rect
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import (
    FinanceTransaction,
    MonthlyTarget,
    Sale,
    Shop,
    Stock,
    User,
)
from app.schemas import MonthlyTargetIn, MonthlyTargetOut
from app.services.metrics import (
    buyer_revenue,
    gross_revenue,
    sale_expenses,
    signed_finance_amount,
    to_decimal,
)
from app.services.sku_resolver import SkuResolver

router = APIRouter()

MP_NAMES = {"wb": "Wildberries", "ozon": "Ozon", "ym": "Яндекс Маркет"}
METRIC_LABELS = {
    "revenue": "Выручка",
    "actual_revenue": "Фактическая выручка",
    "expenses": "Расходы",
    "gross_profit": "Валовая прибыль",
    "net_profit": "Чистая прибыль",
    "drr": "ДРР",
    "orders": "Заказы и товары",
    "returns": "Возвраты",
    "average_check": "Средний чек",
    "products": "Топ товаров",
    "unit_economics": "Юнит-экономика",
    "daily_trend": "Динамика по дням",
}
DEFAULT_METRICS = list(METRIC_LABELS)

# --- Monthly analytics thresholds (P1: месячное закрытие) ---
# ABC-классификация прибыльности. Пороги зеркалят ALERT_THRESHOLDS из
# dashboard.py (min_margin = 15%), плюс фиксированные пороги оборота/ДРР/возвратов.
ABC_MIN_MARGIN = Decimal("15")  # % — маржа ниже порога → класс B
ABC_MAX_DRR = Decimal("15")  # % — ДРР выше порога → класс B
ABC_MAX_RETURN_RATE = Decimal("15")  # % — доля возвратов выше порога → класс B
ABC_MIN_REVENUE = Decimal("30000")  # ₽ — выручка ниже порога оборота → класс C

# Сверка «Продажи / Начисления / Выплаты».
FINANCE_CONFIRMATION_DELAY_DAYS = 2  # окно подтверждения финансов площадками
RECONCILIATION_TOLERANCE = Decimal("2")  # % расхождения, выше которого период «с расхождениями»

# План-факт.
PLAN_FACT_ON_TRACK = Decimal("5")  # % отклонения — в пределах нормы
PLAN_FACT_AT_RISK = Decimal("15")  # % отклонения — зона риска, выше — провал

# Маппинг category FinanceTransaction → категории сверки.
# Адаптеры (ozon.py _FEE_TYPE_CATEGORIES, yandex_market.py) пишут в category
# значения: commission, logistics, storage, returns, advertising, acquiring,
# insurance, other. В сверке нет отдельных строк для эквайринга/страховки/
# хранения — они агрегируются: acquiring/insurance/other → «Комиссии»
# (процентные удержания и прочие списания площадки), storage → «Логистика».
FINANCE_CATEGORY_GROUPS = {
    "revenue": frozenset({"revenue"}),
    "commission": frozenset({"commission", "acquiring", "insurance", "other"}),
    "logistics": frozenset({"logistics", "storage"}),
    "advertising": frozenset({"advertising"}),
    "returns": frozenset({"returns"}),
}
# Типы операций выплат (FinanceTransaction.operation_type). Адаптеры пока таких
# строк не создают — тогда колонка «Выплаты» в сверке недоступна.
PAYOUT_OPERATION_TYPES = frozenset({"payout", "withdrawal", "payment"})

RECONCILIATION_CATEGORIES = [
    ("revenue", "Выручка"),
    ("commission", "Комиссии"),
    ("logistics", "Логистика"),
    ("advertising", "Реклама"),
    ("returns", "Возвраты"),
    ("profit", "Прибыль"),
]


class ReportRequest(BaseModel):
    start_date: date
    end_date: date
    marketplaces: list[str] = Field(default_factory=lambda: ["all"])
    metrics: list[str] = Field(default_factory=lambda: DEFAULT_METRICS.copy())

    @field_validator("end_date")
    @classmethod
    def end_after_start(cls, value: date, info):
        if info.data.get("start_date") and value < info.data["start_date"]:
            raise ValueError("end_date must be on or after start_date")
        return value

    @field_validator("marketplaces")
    @classmethod
    def valid_marketplaces(cls, values):
        allowed = {"all", "wb", "ozon", "ym"}
        if not values or not set(values).issubset(allowed):
            raise ValueError("Unknown marketplace")
        return values

    @field_validator("metrics")
    @classmethod
    def valid_metrics(cls, values):
        if not values or not set(values).issubset(METRIC_LABELS):
            raise ValueError("Unknown report metric")
        return values


def _money(value: Decimal) -> str:
    return f"{value:,.0f} ₽".replace(",", " ")


def _font_name() -> str:
    name = "ReportFont"
    if name in pdfmetrics.getRegisteredFontNames():
        return name
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            pdfmetrics.registerFont(TTFont(name, candidate))
            return name
    raise RuntimeError("Cyrillic font is not installed")


def _read_ozon_unit_xlsx(start_date: date, end_date: date) -> list[dict[str, str]]:
    """Read the official Ozon unit-economics export when it is available."""
    filename = f"Юнит-экономика_{start_date:%d.%m.%Y}-{end_date:%d.%m.%Y}.xlsx"
    path = Path(__file__).resolve().parents[2] / filename
    if not path.exists():
        return []

    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(path) as archive:
        strings_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        strings = [
            "".join(text.text or "" for text in item.iter(namespace + "t"))
            for item in strings_root.findall(namespace + "si")
        ]
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        rows = []
        for xml_row in sheet.iter(namespace + "row"):
            values = []
            for cell in xml_row.findall(namespace + "c"):
                value = cell.find(namespace + "v")
                text = "" if value is None else value.text
                if cell.attrib.get("t") == "s" and text:
                    text = strings[int(text)]
                values.append(text)
            if values:
                rows.append(values)

    header_index = next((index for index, row in enumerate(rows) if "SKU" in row), None)
    if header_index is None:
        return []
    header = rows[header_index]
    return [
        {header[index]: row[index] for index in range(min(len(header), len(row)))}
        for row in rows[header_index + 1 :]
        if len(row) > 2 and row[2]
    ]


def _decimal(value: str) -> Decimal:
    try:
        return Decimal(value or "0")
    except ArithmeticError:
        return Decimal(0)


def _official_ozon_data(rows: list[dict[str, str]]) -> dict[str, Any]:
    """Convert Ozon's own export columns to the report contract."""
    unit_rows = []
    revenue = Decimal(0)
    cost = Decimal(0)
    profit = Decimal(0)
    ordered = delivered = returned = 0
    for row in rows:
        row_profit = _decimal(row.get("Прибыль за период"))
        row_revenue = (
            _decimal(row.get("Выручка"))
            + _decimal(row.get("Баллы за скидки"))
            + _decimal(row.get("Программы партнёров"))
        )
        row_cost = _decimal(row.get("Себестоимость"))
        row_delivered = int(_decimal(row.get("Доставлено товаров, шт")))
        row_ordered = int(_decimal(row.get("Заказано товаров, шт")))
        row_returned = int(_decimal(row.get("Возвращено товаров, шт")))
        revenue += row_revenue
        cost += row_cost * row_delivered
        profit += row_profit
        ordered += row_ordered
        delivered += row_delivered
        returned += row_returned
        unit_rows.append(
            {
                "sku": row.get("Артикул", row.get("SKU", "")),
                "name": row.get("Название товара", ""),
                "cost": row_cost,
                "orders": row_ordered,
                "items": row_delivered,
                "share": _decimal(row.get("Доля от продаж")) * 100,
                "profit_per_unit": _decimal(row.get("Прибыль за шт")),
                "profit_period": row_profit.quantize(Decimal("0.01")),
                "gross_price": (
                    row_revenue / row_delivered if row_delivered else Decimal(0)
                ),
                "actual_price": (
                    _decimal(row.get("Выручка")) / row_delivered
                    if row_delivered
                    else Decimal(0)
                ),
                "expense_per_unit": (
                    (row_revenue - row_cost * row_delivered - row_profit)
                    / row_delivered
                    if row_delivered
                    else Decimal(0)
                ),
                "drr": Decimal(0),
            }
        )
    expenses = revenue - cost - profit
    return {
        "revenue": revenue,
        "actual_revenue": sum(
            (_decimal(row.get("Выручка")) for row in rows), Decimal(0)
        ),
        "expenses": expenses,
        "gross_profit": revenue - expenses,
        "net_profit": profit,
        "orders": ordered,
        "items": delivered,
        "returns": returned,
        "unit_economics": unit_rows,
    }


async def _report_data(
    request: ReportRequest, user: User, db: AsyncSession
) -> dict[str, Any]:
    start = datetime.combine(request.start_date, datetime.min.time())
    end = datetime.combine(request.end_date + timedelta(days=1), datetime.min.time())
    result = await db.execute(select(Shop).where(Shop.user_id == user.id))
    shops = result.scalars().all()
    selected_mp = set(request.marketplaces)
    if "all" not in selected_mp:
        shops = [shop for shop in shops if shop.marketplace.value in selected_mp]
    shop_ids = [shop.id for shop in shops]
    if not shop_ids:
        raise HTTPException(
            status_code=400, detail="Нет магазинов для выбранных площадок"
        )

    sales = (
        (
            await db.execute(
                select(Sale).where(
                    Sale.shop_id.in_(shop_ids),
                    Sale.date >= start,
                    Sale.date < end,
                )
            )
        )
        .scalars()
        .all()
    )
    resolver = await SkuResolver.create(db, user.id, shop_ids)
    ozon_ids = [shop.id for shop in shops if shop.marketplace.value == "ozon"]
    finance = []
    if ozon_ids:
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
        )

    regular = [sale for sale in sales if not sale.is_return]
    returns = [sale for sale in sales if sale.is_return]
    finance_by_shop = defaultdict(Decimal)
    ads_by_shop = defaultdict(Decimal)
    for transaction in finance:
        finance_by_shop[transaction.shop_id] -= signed_finance_amount(transaction)
        if transaction.category == "advertising":
            ads_by_shop[transaction.shop_id] -= signed_finance_amount(transaction)

    by_mp = []
    expense_structure = defaultdict(Decimal)
    total_expenses = Decimal(0)
    total_cost = Decimal(0)
    for shop in shops:
        mp_sales = [sale for sale in regular if sale.shop_id == shop.id]
        mp_returns = [sale for sale in returns if sale.shop_id == shop.id]
        revenue = sum((gross_revenue(sale) for sale in mp_sales), Decimal(0))
        revenue -= sum((gross_revenue(sale) for sale in mp_returns), Decimal(0))
        actual = sum((buyer_revenue(sale) for sale in mp_sales), Decimal(0))
        actual -= sum((buyer_revenue(sale) for sale in mp_returns), Decimal(0))
        if shop.marketplace.value == "ozon":
            expenses = finance_by_shop[shop.id]
            ads = ads_by_shop[shop.id]
        else:
            expenses = sum(
                (sale_expenses(sale) for sale in mp_sales + mp_returns), Decimal(0)
            )
            ads = sum((to_decimal(sale.advertising) for sale in mp_sales), Decimal(0))
            for sale in mp_sales + mp_returns:
                for key in (
                    "commission",
                    "logistics",
                    "storage",
                    "advertising",
                    "returns",
                    "other",
                ):
                    expense_structure[key] += max(
                        to_decimal(getattr(sale, key)), Decimal(0)
                    )
        cost = Decimal(0)
        for sale in mp_sales:
            product = resolver.resolve(sale.shop_id, sale.external_sku)
            if product is not None:
                cost += to_decimal(product.cost_price) * (sale.quantity or 0)
        total_expenses += expenses
        total_cost += cost
        by_mp.append(
            {
                "key": shop.marketplace.value,
                "marketplace": MP_NAMES[shop.marketplace.value],
                "revenue": revenue,
                "actual_revenue": actual,
                "expenses": expenses,
                "gross_profit": revenue - expenses,
                "net_profit": revenue - expenses - cost,
                "drr": ads / revenue * 100 if revenue else Decimal(0),
                "orders": len({sale.external_id for sale in mp_sales}),
                "items": sum(sale.quantity or 0 for sale in mp_sales),
                "returns": len({sale.external_id for sale in mp_returns}),
            }
        )

    revenue = sum((item["revenue"] for item in by_mp), Decimal(0))
    actual = sum((item["actual_revenue"] for item in by_mp), Decimal(0))
    gross = revenue - total_expenses
    net = gross - total_cost
    daily = defaultdict(lambda: {"revenue": Decimal(0), "orders": set()})
    for sale in regular:
        day = sale.date.date().isoformat()
        daily[day]["revenue"] += gross_revenue(sale)
        daily[day]["orders"].add(sale.external_id)
    top_products = defaultdict(lambda: {"name": "", "units": 0, "revenue": Decimal(0)})
    unit_economics = defaultdict(
        lambda: {
            "name": "",
            "cost": Decimal(0),
            "orders": set(),
            "items": 0,
            "revenue": Decimal(0),
            "returns_revenue": Decimal(0),
            "expenses": Decimal(0),
            "shared_expenses": Decimal(0),
        }
    )
    def _canonical_key(sale: Sale) -> str:
        """Aggregate key: canonical product sku, or the bare external SKU."""
        product = resolver.resolve(sale.shop_id, sale.external_sku)
        return product.sku if product is not None else sale.external_sku

    for sale in regular + returns:
        key = _canonical_key(sale)
        row = top_products[key]
        unit_row = unit_economics[key]
        product = resolver.resolve(sale.shop_id, sale.external_sku)
        row["name"] = product.name if product is not None else sale.external_sku
        unit_row["name"] = row["name"]
        unit_row["cost"] = (
            to_decimal(product.cost_price) if product is not None else Decimal(0)
        )
        if sale.is_return:
            unit_row["returns_revenue"] += gross_revenue(sale)
        else:
            unit_row["orders"].add(sale.external_id)
            unit_row["items"] += sale.quantity or 0
            unit_row["revenue"] += gross_revenue(sale)
            row["units"] += sale.quantity or 0
            row["revenue"] += gross_revenue(sale)

        if shop_mp := next(
            (shop.marketplace.value for shop in shops if shop.id == sale.shop_id),
            None,
        ):
            if shop_mp != "ozon":
                unit_row["expenses"] += sale_expenses(sale)

    # Ozon expenses are stored as shop-level finance transactions. Allocate them
    # to SKUs by each SKU's share of the shop's gross seller revenue.
    for shop in shops:
        if shop.marketplace.value != "ozon":
            continue
        shop_sales = [sale for sale in regular if sale.shop_id == shop.id]
        shop_revenue = sum((gross_revenue(sale) for sale in shop_sales), Decimal(0))
        if not shop_revenue:
            continue
        for key in {_canonical_key(sale) for sale in shop_sales}:
            sku_revenue = sum(
                (
                    gross_revenue(sale)
                    for sale in shop_sales
                    if _canonical_key(sale) == key
                ),
                Decimal(0),
            )
            unit_economics[sku]["shared_expenses"] += (
                finance_by_shop[shop.id] * sku_revenue / shop_revenue
            )

    total_items = sum(item["items"] for item in unit_economics.values())
    unit_rows = []
    for item in unit_economics.values():
        profit_period = (
            item["revenue"]
            - item["returns_revenue"]
            - item["expenses"]
            - item["shared_expenses"]
            - item["cost"] * item["items"]
        )
        unit_rows.append(
            {
                "name": item["name"],
                "cost": item["cost"],
                "orders": len(item["orders"]),
                "items": item["items"],
                "share": (
                    Decimal(item["items"]) / Decimal(total_items) * 100
                    if total_items
                    else Decimal(0)
                ),
                "profit_per_unit": (
                    profit_period / item["items"] if item["items"] else Decimal(0)
                ),
                "profit_period": profit_period,
            }
        )
    unit_rows.sort(key=lambda row: row["profit_period"], reverse=True)
    for row in unit_rows:
        row["profit_period"] = row["profit_period"].quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        row["profit_per_unit"] = (
            row["profit_period"] / row["items"] if row["items"] else Decimal(0)
        )
    if unit_rows:
        rounded_unit_profit = sum(
            (row["profit_period"] for row in unit_rows), Decimal(0)
        )
        unit_rows[-1]["profit_period"] += net - rounded_unit_profit
        unit_rows[-1]["profit_per_unit"] = (
            unit_rows[-1]["profit_period"] / unit_rows[-1]["items"]
            if unit_rows[-1]["items"]
            else Decimal(0)
        )

    report_source = "database"
    report_orders = len({sale.external_id for sale in regular})
    report_items = sum(sale.quantity or 0 for sale in regular)
    report_returns = len({sale.external_id for sale in returns})
    return {
        "period": {
            "start": request.start_date.isoformat(),
            "end": request.end_date.isoformat(),
        },
        "source": report_source,
        "metrics": request.metrics,
        "kpi": {
            "revenue": revenue,
            "actual_revenue": actual,
            "expenses": total_expenses,
            "gross_profit": gross,
            "net_profit": net,
            "drr": (
                sum(
                    (row["drr"] * row["revenue"] / revenue for row in by_mp), Decimal(0)
                )
                if revenue
                else Decimal(0)
            ),
            "orders": report_orders,
            "items": report_items,
            "returns": report_returns,
            "average_check": (
                actual / len({sale.external_id for sale in regular})
                if regular
                else Decimal(0)
            ),
        },
        "by_marketplace": by_mp,
        "daily": [
            {"date": day, "revenue": values["revenue"], "orders": len(values["orders"])}
            for day, values in sorted(daily.items())
        ],
        "top_products": sorted(
            top_products.values(), key=lambda row: row["revenue"], reverse=True
        )[:20],
        "unit_economics": sorted(
            unit_rows, key=lambda row: row["profit_period"], reverse=True
        ),
        "expense_structure": dict(expense_structure),
    }


def _json_safe(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _build_pdf(data: dict[str, Any]) -> bytes:
    font = _font_name()
    navy = colors.HexColor("#111827")
    blue = colors.HexColor("#2563EB")
    muted = colors.HexColor("#6B7280")
    page_bg = colors.HexColor("#F3F4F6")
    panel_bg = colors.white
    border = colors.HexColor("#E5E7EB")
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="ReportTitle",
            parent=styles["Title"],
            fontName=font,
            fontSize=22,
            alignment=0,
            textColor=navy,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Section",
            parent=styles["Heading2"],
            fontName=font,
            fontSize=13,
            textColor=navy,
            spaceBefore=8,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Body",
            parent=styles["BodyText"],
            fontName=font,
            fontSize=8.5,
            leading=11,
            textColor=muted,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Eyebrow",
            parent=styles["BodyText"],
            fontName=font,
            fontSize=7.5,
            leading=9,
            textColor=blue,
            spaceAfter=2,
        )
    )
    styles.add(
        ParagraphStyle(
            name="KpiLabel",
            parent=styles["BodyText"],
            fontName=font,
            fontSize=7.5,
            leading=9,
            textColor=muted,
        )
    )
    styles.add(
        ParagraphStyle(
            name="KpiValue",
            parent=styles["BodyText"],
            fontName=font,
            fontSize=13,
            leading=16,
            textColor=navy,
        )
    )
    story = [
        Paragraph("LOCIUM ANALYTICS", styles["Eyebrow"]),
        Paragraph("Отчёт о продажах", styles["ReportTitle"]),
        Spacer(1, 1 * mm),
        Paragraph(
            f"Период: {data['period']['start']} — {data['period']['end']}",
            styles["Body"],
        ),
        Paragraph(
            (
                "Источник: официальный отчёт Ozon"
                if data.get("source") == "ozon_unit_economics_export"
                else "Источник: данные аналитической базы"
            ),
            styles["Eyebrow"],
        ),
        Spacer(1, 5 * mm),
    ]
    kpi = data["kpi"]
    kpi_keys = [key for key in data["metrics"] if key in kpi]
    kpi_cards = []
    for key in kpi_keys:
        if key == "drr":
            value = f"{kpi[key]:.1f}%"
        elif key in {"orders", "returns"}:
            value = f"{int(kpi[key]):,}".replace(",", " ")
        else:
            value = _money(kpi[key])
        kpi_cards.append(
            [
                Paragraph(METRIC_LABELS[key], styles["KpiLabel"]),
                Paragraph(value, styles["KpiValue"]),
            ]
        )
    kpi_rows = [kpi_cards[index : index + 2] for index in range(0, len(kpi_cards), 2)]
    if kpi_rows and len(kpi_rows[-1]) == 1:
        kpi_rows[-1].append("")
    if kpi_rows:
        story.append(Paragraph("Основные показатели", styles["Section"]))
        story.append(
            Table(
                kpi_rows,
                colWidths=[80 * mm, 80 * mm],
                style=TableStyle(
                    [
                        ("FONTNAME", (0, 0), (-1, -1), font),
                        ("BACKGROUND", (0, 0), (-1, -1), panel_bg),
                        ("BOX", (0, 0), (-1, -1), 0.6, border),
                        ("INNERGRID", (0, 0), (-1, -1), 0.6, border),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 9),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                        ("TOPPADDING", (0, 0), (-1, -1), 8),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                    ]
                ),
            )
        )
    story.append(Paragraph("Разбивка по маркетплейсам", styles["Section"]))
    marketplace_chart = Drawing(500, 170)
    marketplace_chart.add(Rect(0, 0, 500, 170, fillColor=panel_bg, strokeColor=border))
    bars = VerticalBarChart()
    bars.x = 45
    bars.y = 30
    bars.width = 430
    bars.height = 120
    bars.data = [[float(row["revenue"]) for row in data["by_marketplace"]]]
    bars.categoryAxis.categoryNames = [
        row["marketplace"] for row in data["by_marketplace"]
    ]
    bars.valueAxis.valueMin = 0
    bars.valueAxis.labels.fontName = font
    bars.categoryAxis.labels.fontName = font
    bars.bars[0].fillColor = colors.HexColor("#2563EB")
    marketplace_chart.add(bars)
    story.append(marketplace_chart)
    rows = [
        [
            Paragraph("Площадка", styles["KpiLabel"]),
            Paragraph("Заказы", styles["KpiLabel"]),
            Paragraph("Шт.", styles["KpiLabel"]),
            Paragraph("Выручка", styles["KpiLabel"]),
            Paragraph("Расходы", styles["KpiLabel"]),
            Paragraph("Валовая прибыль<br/>(деньги на счёт)", styles["KpiLabel"]),
            Paragraph("Прибыль", styles["KpiLabel"]),
            Paragraph("ДРР", styles["KpiLabel"]),
        ]
    ]
    for row in data["by_marketplace"]:
        rows.append(
            [
                row["marketplace"],
                str(row["orders"]),
                str(row["items"]),
                _money(row["revenue"]),
                _money(row["expenses"]),
                _money(row["gross_profit"]),
                _money(row["net_profit"]),
                f"{row['drr']:.1f}%",
            ]
        )
    story.append(
        Table(
            rows,
            colWidths=[
                25 * mm,
                15 * mm,
                13 * mm,
                24 * mm,
                24 * mm,
                29 * mm,
                27 * mm,
                18 * mm,
            ],
            style=TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), font),
                    ("FONTSIZE", (0, 0), (-1, -1), 7),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EFF6FF")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), navy),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.8, border),
                    ("LINEBELOW", (0, 1), (-1, -2), 0.4, border),
                    ("BOX", (0, 0), (-1, -1), 0.6, border),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ]
            ),
        )
    )
    if "products" in data["metrics"]:
        story.append(PageBreak())
        story.append(Paragraph("Топ товаров", styles["Section"]))
        rows = [["#", "Товар", "Шт.", "Выручка"]]
        for index, row in enumerate(data["top_products"], 1):
            rows.append(
                [
                    str(index),
                    Paragraph(escape(row["name"]), styles["Body"]),
                    str(row["units"]),
                    _money(row["revenue"]),
                ]
            )
        story.append(
            Table(
                rows,
                colWidths=[10 * mm, 105 * mm, 25 * mm, 35 * mm],
                style=TableStyle(
                    [
                        ("FONTNAME", (0, 0), (-1, -1), font),
                        ("FONTSIZE", (0, 0), (-1, -1), 8),
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EFF6FF")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), navy),
                        ("LINEBELOW", (0, 0), (-1, 0), 0.8, border),
                        ("LINEBELOW", (0, 1), (-1, -2), 0.4, border),
                        ("BOX", (0, 0), (-1, -1), 0.6, border),
                    ]
                ),
            )
        )
    if "unit_economics" in data["metrics"]:
        story.append(PageBreak())
        story.append(Paragraph("Юнит-экономика", styles["Section"]))
        unit_rows = [
            [
                "Название товара",
                "Себестоимость",
                "Заказано",
                "Доставлено, шт",
                "Доля от продаж",
                "Прибыль за шт",
                "Прибыль за период",
            ]
        ]
        for row in data["unit_economics"]:
            profit_color = "#15803D" if row["profit_per_unit"] >= 0 else "#DC2626"
            unit_rows.append(
                [
                    Paragraph(row["name"][:65], styles["Body"]),
                    _money(row["cost"]),
                    str(row["orders"]),
                    str(row["items"]),
                    f"{row['share']:.1f}%",
                    Paragraph(
                        f'<font color="{profit_color}">{_money(row["profit_per_unit"])}</font>',
                        styles["Body"],
                    ),
                    Paragraph(
                        f'<font color="{profit_color}">{_money(row["profit_period"])}</font>',
                        styles["Body"],
                    ),
                ]
            )
        story.append(
            Table(
                unit_rows,
                colWidths=[
                    49 * mm,
                    24 * mm,
                    18 * mm,
                    24 * mm,
                    23 * mm,
                    25 * mm,
                    29 * mm,
                ],
                repeatRows=1,
                style=TableStyle(
                    [
                        ("FONTNAME", (0, 0), (-1, -1), font),
                        ("FONTSIZE", (0, 0), (-1, -1), 7),
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EFF6FF")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), navy),
                        ("LINEBELOW", (0, 0), (-1, 0), 0.8, border),
                        ("LINEBELOW", (0, 1), (-1, -2), 0.4, border),
                        ("BOX", (0, 0), (-1, -1), 0.6, border),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 4),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ]
                ),
            )
        )
    if "daily_trend" in data["metrics"]:
        story.append(Paragraph("Динамика выручки", styles["Section"]))
        chart = Drawing(500, 180)
        chart.add(Rect(0, 0, 500, 180, fillColor=panel_bg, strokeColor=border))
        plot = LinePlot()
        plot.x = 45
        plot.y = 30
        plot.width = 430
        plot.height = 125
        plot.data = [
            [
                (index, float(row["revenue"]))
                for index, row in enumerate(data["daily"], 1)
            ]
        ]
        plot.lines[0].strokeColor = colors.HexColor("#2563EB")
        plot.lines[0].strokeWidth = 2
        plot.xValueAxis.valueMin = 1
        plot.xValueAxis.valueMax = max(len(data["daily"]), 1)
        plot.yValueAxis.valueMin = 0
        plot.xValueAxis.labels.fontName = font
        plot.yValueAxis.labels.fontName = font
        chart.add(plot)
        story.append(chart)
        rows = [["Дата", "Заказы", "Выручка"]] + [
            [row["date"], str(row["orders"]), _money(row["revenue"])]
            for row in data["daily"]
        ]
        story.append(
            Table(
                rows,
                colWidths=[45 * mm, 40 * mm, 75 * mm],
                repeatRows=1,
                style=TableStyle(
                    [
                        ("FONTNAME", (0, 0), (-1, -1), font),
                        ("FONTSIZE", (0, 0), (-1, -1), 7),
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EFF6FF")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), navy),
                        ("LINEBELOW", (0, 0), (-1, 0), 0.8, border),
                        ("LINEBELOW", (0, 1), (-1, -2), 0.4, border),
                        ("BOX", (0, 0), (-1, -1), 0.6, border),
                    ]
                ),
            )
        )
    buffer = BytesIO()

    def draw_page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(page_bg)
        canvas.rect(0, 0, A4[0], A4[1], fill=1, stroke=0)
        canvas.setFillColor(panel_bg)
        canvas.rect(0, A4[1] - 14 * mm, A4[0], 14 * mm, fill=1, stroke=0)
        canvas.setFillColor(blue)
        canvas.rect(0, A4[1] - 14 * mm, A4[0], 1.5 * mm, fill=1, stroke=0)
        canvas.setFillColor(muted)
        canvas.setFont(font, 7)
        canvas.drawRightString(
            A4[0] - 14 * mm, 8 * mm, f"Locium Analytics · {doc.page}"
        )
        canvas.restoreState()

    SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=14 * mm,
        leftMargin=14 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
    ).build(story, onFirstPage=draw_page, onLaterPages=draw_page)
    return buffer.getvalue()


@router.post("/preview")
async def preview_report(
    request: ReportRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _json_safe(await _report_data(request, user, db))


@router.post("/pdf")
async def download_report(
    request: ReportRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    data = await _report_data(request, user, db)
    pdf = _build_pdf(data)
    filename = f"sales-report-{request.start_date}-{request.end_date}.pdf"
    return StreamingResponse(
        BytesIO(pdf),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


# ---------------------------------------------------------------------------
# P1. Месячное закрытие и аналитика
# ---------------------------------------------------------------------------

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _parse_month(month: str) -> tuple[date, date]:
    """Validate 'YYYY-MM' and return (first day, first day of next month)."""
    if not _MONTH_RE.match(month or ""):
        raise HTTPException(
            status_code=422, detail="month должен быть в формате YYYY-MM"
        )
    year, mon = int(month[:4]), int(month[5:7])
    start = date(year, mon, 1)
    end = date(year + 1, 1, 1) if mon == 12 else date(year, mon + 1, 1)
    return start, end


async def _load_month_data(db: AsyncSession, user: User, start: date, end: date):
    """User's shops, month sales and finance transactions."""
    result = await db.execute(select(Shop).where(Shop.user_id == user.id))
    shops = result.scalars().all()
    shop_ids = [shop.id for shop in shops]
    start_dt = datetime.combine(start, datetime.min.time())
    end_dt = datetime.combine(end, datetime.min.time())
    sales, finance = [], []
    if shop_ids:
        sales = (
            (
                await db.execute(
                    select(Sale).where(
                        Sale.shop_id.in_(shop_ids),
                        Sale.date >= start_dt,
                        Sale.date < end_dt,
                    )
                )
            )
            .scalars()
            .all()
        )
        finance = (
            (
                await db.execute(
                    select(FinanceTransaction).where(
                        FinanceTransaction.shop_id.in_(shop_ids),
                        FinanceTransaction.operation_date >= start_dt,
                        FinanceTransaction.operation_date < end_dt,
                    )
                )
            )
            .scalars()
            .all()
        )
    return shops, sales, finance


@router.get("/abc-classification")
async def abc_classification(
    month: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Месячная ABC-классификация SKU по прибыльности.

    Правила (пороги — константы ABC_* в начале модуля):
      D — остаток > 0 и продаж за месяц нет;
      C — прибыль < 0 или выручка ниже порога оборота ABC_MIN_REVENUE;
      B — прибыль > 0, но маржа < ABC_MIN_MARGIN, либо доля возвратов
          > ABC_MAX_RETURN_RATE, либо ДРР > ABC_MAX_DRR;
      A — всё остальное (прибыль есть, маржа и показатели в норме).
    """
    start, end = _parse_month(month)
    shops, sales, _ = await _load_month_data(db, user, start, end)
    shop_ids = [shop.id for shop in shops]
    shop_marketplaces = {shop.id: shop.marketplace.value for shop in shops}
    resolver = await SkuResolver.create(db, user.id, shop_ids or None)

    # Актуальные остатки на конец месяца: последняя запись по (магазин, sku).
    stock_by_key: dict[tuple, Decimal] = defaultdict(Decimal)
    if shop_ids:
        result = await db.execute(
            select(
                Stock.shop_id,
                Stock.external_sku,
                func.max(Stock.date).label("last_date"),
            )
            .where(Stock.shop_id.in_(shop_ids), Stock.date < end)
            .group_by(Stock.shop_id, Stock.external_sku)
        )
        latest = {(row.shop_id, row.external_sku): row.last_date for row in result.all()}
        for (shop_id, sku), last_date in latest.items():
            row = (
                await db.execute(
                    select(func.coalesce(func.sum(Stock.quantity), 0)).where(
                        Stock.shop_id == shop_id,
                        Stock.external_sku == sku,
                        Stock.date == last_date,
                    )
                )
            ).scalar_one()
            stock_by_key[(shop_id, sku)] = to_decimal(row)

    def _canonical_key(shop_id, external_sku) -> str:
        product = resolver.resolve(shop_id, external_sku)
        return product.sku if product is not None else external_sku

    items: dict[str, dict[str, Any]] = {}

    def _item(key: str) -> dict[str, Any]:
        row = items.get(key)
        if row is None:
            product = resolver.resolve_sku(key)
            row = {
                "sku": key,
                "name": product.name if product is not None else key,
                "revenue": Decimal(0),
                "expenses": Decimal(0),
                "cost": Decimal(0),
                "advertising": Decimal(0),
                "sold_qty": 0,
                "returned_qty": 0,
                "orders": set(),
                "stock": Decimal(0),
                "marketplaces": set(),
            }
            items[key] = row
        return row

    for sale in sales:
        key = _canonical_key(sale.shop_id, sale.external_sku)
        row = _item(key)
        row["marketplaces"].add(shop_marketplaces.get(sale.shop_id, ""))
        if sale.is_return:
            row["returned_qty"] += sale.quantity or 0
            row["revenue"] -= gross_revenue(sale)
            row["expenses"] += sale_expenses(sale)
            continue
        row["orders"].add(sale.external_id)
        row["sold_qty"] += sale.quantity or 0
        row["revenue"] += gross_revenue(sale)
        row["expenses"] += sale_expenses(sale)
        row["advertising"] += to_decimal(sale.advertising)
        product = resolver.resolve(sale.shop_id, sale.external_sku)
        if product is not None:
            row["cost"] += to_decimal(product.cost_price) * (sale.quantity or 0)

    for (shop_id, sku), qty in stock_by_key.items():
        if qty <= 0:
            continue
        row = _item(_canonical_key(shop_id, sku))
        row["stock"] += qty
        row["marketplaces"].add(shop_marketplaces.get(shop_id, ""))

    rows = []
    for row in items.values():
        profit = row["revenue"] - row["expenses"] - row["cost"]
        margin = profit / row["revenue"] * 100 if row["revenue"] else Decimal(0)
        drr = (
            row["advertising"] / row["revenue"] * 100 if row["revenue"] else Decimal(0)
        )
        return_rate = (
            Decimal(row["returned_qty"]) / Decimal(row["sold_qty"]) * 100
            if row["sold_qty"]
            else Decimal(0)
        )
        if row["stock"] > 0 and row["sold_qty"] == 0:
            abc_class = "D"
        elif profit < 0 or row["revenue"] < ABC_MIN_REVENUE:
            abc_class = "C"
        elif profit > 0 and (
            margin < ABC_MIN_MARGIN
            or drr > ABC_MAX_DRR
            or return_rate > ABC_MAX_RETURN_RATE
        ):
            abc_class = "B"
        else:
            abc_class = "A"
        rows.append(
            {
                "sku": row["sku"],
                "name": row["name"],
                "class": abc_class,
                "revenue": row["revenue"],
                "profit": profit,
                "margin_percent": margin,
                "drr_percent": drr,
                "return_rate_percent": return_rate,
                "items_sold": row["sold_qty"],
                "returns": row["returned_qty"],
                "orders": len(row["orders"]),
                "stock": int(row["stock"]),
                "marketplaces": sorted(m for m in row["marketplaces"] if m),
            }
        )

    rows.sort(key=lambda item: item["profit"], reverse=True)
    summary = {"A": 0, "B": 0, "C": 0, "D": 0}
    for row in rows:
        summary[row["class"]] += 1
    total_profit = sum((row["profit"] for row in rows), Decimal(0))
    profit_a = sum((row["profit"] for row in rows if row["class"] == "A"), Decimal(0))
    return _json_safe(
        {
            "month": month,
            "thresholds": {
                "min_margin_percent": ABC_MIN_MARGIN,
                "max_drr_percent": ABC_MAX_DRR,
                "max_return_rate_percent": ABC_MAX_RETURN_RATE,
                "min_revenue": ABC_MIN_REVENUE,
            },
            "summary": summary,
            "a_profit_share_percent": (
                profit_a / total_profit * 100 if total_profit > 0 else Decimal(0)
            ),
            "items": rows,
        }
    )


def _sales_category_amounts(sales: list[Sale]) -> dict[str, Decimal]:
    """Сторона «Продажи» (таблица Sale) по категориям сверки.

    «Возвраты» — только колонка returns: выручка возвратов уже вычтена
    из «Выручки», иначе был бы двойной учёт. «Прибыль» до себестоимости,
    чтобы сходилась арифметика сверки (без себестоимости в начислениях нет).
    """
    amounts = {key: Decimal(0) for key, _ in RECONCILIATION_CATEGORIES}
    for sale in sales:
        if sale.is_return:
            amounts["revenue"] -= gross_revenue(sale)
        else:
            amounts["revenue"] += gross_revenue(sale)
        amounts["commission"] += (
            to_decimal(sale.commission)
            + to_decimal(sale.acquiring)
            + to_decimal(sale.insurance)
            + to_decimal(sale.other)
        )
        amounts["logistics"] += to_decimal(sale.logistics) + to_decimal(sale.storage)
        amounts["advertising"] += to_decimal(sale.advertising)
        amounts["returns"] += to_decimal(sale.returns)
    amounts["profit"] = (
        amounts["revenue"]
        - amounts["commission"]
        - amounts["logistics"]
        - amounts["advertising"]
        - amounts["returns"]
    )
    return amounts


def _finance_category_amounts(
    finance: list[FinanceTransaction],
) -> tuple[dict[str, Decimal], Decimal]:
    """Сторона «Начисления» (FinanceTransaction) и сумма выплат.

    Начисления хранятся в знаковой конвенции signed_finance_amount (расходы
    отрицательные), поэтому для сверки суммы приводятся к положительным.
    """
    amounts = {key: Decimal(0) for key, _ in RECONCILIATION_CATEGORIES}
    payouts = Decimal(0)
    for tx in finance:
        signed = signed_finance_amount(tx)
        op_type = (tx.operation_type or "").lower()
        if any(token in op_type for token in PAYOUT_OPERATION_TYPES):
            payouts += abs(to_decimal(tx.amount))
            continue
        for category, group in FINANCE_CATEGORY_GROUPS.items():
            if tx.category in group:
                # Расходы хранятся отрицательными, начисления выручки — положительными.
                amounts[category] += signed if category == "revenue" else -signed
                break
        amounts["profit"] += signed
    return amounts, payouts


@router.get("/reconciliation")
async def reconciliation(
    month: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Сверка «Продажи / Начисления / Выплаты» за месяц.

    Статус периода: «preliminary» — месяц новее окна подтверждения финансов
    площадками (FINANCE_CONFIRMATION_DELAY_DAYS); «has_discrepancies» —
    расхождение по любой строке больше RECONCILIATION_TOLERANCE %; иначе —
    «closed». Распределённые расходы оценочные, не бухгалтерские (note).
    """
    start, end = _parse_month(month)
    shops, sales, finance = await _load_month_data(db, user, start, end)

    groups: dict[str, dict[str, Any]] = {}
    for shop in shops:
        groups.setdefault(
            shop.marketplace.value,
            {"marketplace": MP_NAMES[shop.marketplace.value], "sales": [], "finance": []},
        )
    for sale in sales:
        groups[shop_mp_of(sale.shop_id, shops)]["sales"].append(sale)
    for tx in finance:
        groups.setdefault(
            tx.marketplace.value,
            {"marketplace": MP_NAMES[tx.marketplace.value], "sales": [], "finance": []},
        )["finance"].append(tx)

    def _build_block(sales_block: list[Sale], finance_block: list[FinanceTransaction]):
        sales_amounts = _sales_category_amounts(sales_block)
        finance_amounts, payouts = _finance_category_amounts(finance_block)
        rows = []
        for key, label in RECONCILIATION_CATEGORIES:
            discrepancy = sales_amounts[key] - finance_amounts[key]
            base = abs(sales_amounts[key])
            rows.append(
                {
                    "key": key,
                    "label": label,
                    "sales": sales_amounts[key],
                    "accruals": finance_amounts[key],
                    "payouts": payouts if key == "profit" else None,
                    "discrepancy": discrepancy,
                    "discrepancy_percent": (
                        discrepancy / base * 100 if base else Decimal(0)
                    ),
                }
            )
        return rows, payouts

    by_marketplace = []
    total_sales, total_finance = [], []
    payouts_total = Decimal(0)
    for key, group in sorted(groups.items()):
        rows, payouts = _build_block(group["sales"], group["finance"])
        by_marketplace.append({"key": key, "marketplace": group["marketplace"], "rows": rows})
        total_sales.extend(group["sales"])
        total_finance.extend(group["finance"])
        payouts_total += payouts
    total_rows, _ = _build_block(total_sales, total_finance)

    now = datetime.utcnow()
    month_end = datetime.combine(end, datetime.min.time())
    if month_end > now - timedelta(days=FINANCE_CONFIRMATION_DELAY_DAYS):
        status = "preliminary"
    elif any(
        abs(row["discrepancy_percent"]) > RECONCILIATION_TOLERANCE
        and abs(row["discrepancy"]) > Decimal("1")
        for row in total_rows
    ):
        status = "has_discrepancies"
    else:
        status = "closed"

    return _json_safe(
        {
            "month": month,
            "status": status,
            "payouts_available": payouts_total > 0,
            "tolerance_percent": RECONCILIATION_TOLERANCE,
            "note": (
                "Распределённые расходы — оценочные, не бухгалтерские. "
                "У площадок расходы и продажи могут иметь разные даты."
            ),
            "by_marketplace": by_marketplace,
            "total": total_rows,
        }
    )


def shop_mp_of(shop_id, shops: list[Shop]) -> str:
    for shop in shops:
        if shop.id == shop_id:
            return shop.marketplace.value
    return ""


async def _get_target(db: AsyncSession, user: User, month_date: date) -> MonthlyTarget:
    result = await db.execute(
        select(MonthlyTarget).where(
            MonthlyTarget.user_id == user.id, MonthlyTarget.month == month_date
        )
    )
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Цели на месяц не заданы")
    return target


@router.put("/monthly-targets/{month}", response_model=MonthlyTargetOut)
async def upsert_monthly_target(
    month: str,
    payload: MonthlyTargetIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Создать или обновить месячные цели (upsert)."""
    start, _ = _parse_month(month)
    result = await db.execute(
        select(MonthlyTarget).where(
            MonthlyTarget.user_id == user.id, MonthlyTarget.month == start
        )
    )
    target = result.scalar_one_or_none()
    if target is None:
        target = MonthlyTarget(user_id=user.id, month=start, targets=dict(payload.targets))
        db.add(target)
    else:
        target.targets = dict(payload.targets)
    target.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(target)
    return target


@router.get("/monthly-targets/{month}", response_model=MonthlyTargetOut)
async def get_monthly_target(
    month: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    start, _ = _parse_month(month)
    return await _get_target(db, user, start)


@router.get("/plan-fact")
async def plan_fact(
    month: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """План-факт по месячным целям.

    Для каждой заданной цели — факт за месяц, отклонение и статус:
    on_track (±PLAN_FACT_ON_TRACK %), at_risk (±5–15 %), off_track (>15 %).
    Отклонение в процентах считается от модуля плана; при плане 0 — null.
    Для ДРР и возвратов отрицательное отклонение — благоприятное.
    Остатки (stock) пока не считаются — строка с пометкой.
    """
    start, end = _parse_month(month)
    target = await _get_target(db, user, start)
    _, sales, finance = await _load_month_data(db, user, start, end)

    revenue = Decimal(0)
    expenses = Decimal(0)
    advertising = Decimal(0)
    sold_qty = 0
    returned_qty = 0
    orders = set()
    resolver = await SkuResolver.create(db, user.id)
    for sale in sales:
        if sale.is_return:
            revenue -= gross_revenue(sale)
            returned_qty += sale.quantity or 0
            expenses += sale_expenses(sale)
            continue
        revenue += gross_revenue(sale)
        expenses += sale_expenses(sale)
        advertising += to_decimal(sale.advertising)
        sold_qty += sale.quantity or 0
        orders.add(sale.external_id)
    cost = Decimal(0)
    for sale in sales:
        if sale.is_return:
            continue
        product = resolver.resolve(sale.shop_id, sale.external_sku)
        if product is not None:
            cost += to_decimal(product.cost_price) * (sale.quantity or 0)
    profit = revenue - expenses - cost
    facts = {
        "revenue": revenue,
        "profit": profit,
        "margin": profit / revenue * 100 if revenue else Decimal(0),
        "drr": advertising / revenue * 100 if revenue else Decimal(0),
        "orders": Decimal(len(orders)),
        "returns": (
            Decimal(returned_qty) / Decimal(sold_qty) * 100 if sold_qty else Decimal(0)
        ),
    }

    labels = {
        "revenue": "Выручка",
        "profit": "Прибыль",
        "margin": "Маржа, %",
        "drr": "ДРР, %",
        "orders": "Заказы",
        "returns": "Возвраты, %",
        "stock": "Остатки, шт",
    }
    rows = []
    for metric, plan_value in (target.targets or {}).items():
        if metric not in labels:
            continue
        row = {
            "metric": metric,
            "label": labels[metric],
            "plan": plan_value,
            "fact": None,
            "deviation": None,
            "deviation_percent": None,
            "status": None,
        }
        if metric == "stock":
            row["note"] = "Факт по остаткам пока не считается"
            rows.append(row)
            continue
        fact_value = facts[metric]
        plan_dec = to_decimal(plan_value)
        deviation = fact_value - plan_dec
        deviation_percent = (
            deviation / abs(plan_dec) * 100 if plan_dec != 0 else None
        )
        if deviation_percent is None:
            status = "on_track" if deviation == 0 else "off_track"
        else:
            abs_dev = abs(deviation_percent)
            if abs_dev <= PLAN_FACT_ON_TRACK:
                status = "on_track"
            elif abs_dev <= PLAN_FACT_AT_RISK:
                status = "at_risk"
            else:
                status = "off_track"
        row.update(
            {
                "fact": fact_value,
                "deviation": deviation,
                "deviation_percent": deviation_percent,
                "status": status,
            }
        )
        rows.append(row)
    return _json_safe(
        {
            "month": month,
            "thresholds": {
                "on_track_percent": PLAN_FACT_ON_TRACK,
                "at_risk_percent": PLAN_FACT_AT_RISK,
            },
            "note": (
                "Для ДРР и возвратов отрицательное отклонение — благоприятное. "
                "Распределённые расходы — оценочные, не бухгалтерские."
            ),
            "rows": rows,
        }
    )
