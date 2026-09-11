"""Configurable sales report preview and PDF export."""

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from pathlib import Path
from typing import Any
import zipfile
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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import FinanceTransaction, Product, Sale, Shop, User
from app.services.metrics import (
    buyer_revenue,
    gross_revenue,
    sale_expenses,
    signed_finance_amount,
    to_decimal,
)

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
    products = {
        product.sku: product
        for product in (
            await db.execute(select(Product).where(Product.user_id == user.id))
        )
        .scalars()
        .all()
    }
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
        cost = sum(
            (
                to_decimal(products[sale.external_sku].cost_price)
                * (sale.quantity or 0)
                for sale in mp_sales
                if sale.external_sku in products
            ),
            Decimal(0),
        )
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
    for sale in regular + returns:
        row = top_products[sale.external_sku]
        unit_row = unit_economics[sale.external_sku]
        row["name"] = (
            products[sale.external_sku].name
            if sale.external_sku in products
            else sale.external_sku
        )
        unit_row["name"] = row["name"]
        unit_row["cost"] = (
            to_decimal(products[sale.external_sku].cost_price)
            if sale.external_sku in products
            else Decimal(0)
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
        for sku in {sale.external_sku for sale in shop_sales}:
            sku_revenue = sum(
                (
                    gross_revenue(sale)
                    for sale in shop_sales
                    if sale.external_sku == sku
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
