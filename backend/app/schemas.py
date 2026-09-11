from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional, List, Dict
from decimal import Decimal
from datetime import datetime, date
from uuid import UUID
from app.models import UserRole, Marketplace


# --- Auth ---
class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6)
    role: UserRole = UserRole.admin


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: UUID
    email: str
    role: UserRole
    created_at: datetime

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# --- Products (Catalog) ---
class ProductCreate(BaseModel):
    sku: str = Field(..., max_length=100)
    name: str = Field(..., max_length=500)
    cost_price: Decimal = Field(default=0, ge=0)
    min_price: Decimal = Field(default=0, ge=0)
    weight_kg: Optional[Decimal] = None
    category: Optional[str] = None


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    cost_price: Optional[Decimal] = None
    min_price: Optional[Decimal] = None
    weight_kg: Optional[Decimal] = None
    category: Optional[str] = None


class ProductCostUpdate(BaseModel):
    cost_price: float = Field(..., ge=0)


class ProductMerge(BaseModel):
    source_skus: List[str]
    target_sku: str


class PriceRecommendation(BaseModel):
    recommended_price: Optional[Decimal] = None
    min_price: Optional[Decimal] = None  # текущая средняя цена продаж
    action: str  # "raise" | "keep" | "lower"


class ProductOut(BaseModel):
    id: UUID
    sku: str
    canonical_sku: Optional[str]
    name: str
    cost_price: Decimal
    min_price: Decimal
    weight_kg: Optional[Decimal]
    category: Optional[str]
    created_at: datetime
    sales_count: int = 0
    total_revenue: Decimal = Decimal("0")
    price_recommendation: Optional[PriceRecommendation] = None

    class Config:
        from_attributes = True


# --- Price history ---
class PriceHistoryPoint(BaseModel):
    price: Decimal
    created_at: datetime

    class Config:
        from_attributes = True


# --- Shops ---
class ShopCreate(BaseModel):
    marketplace: Marketplace
    name: str = Field(..., max_length=255)
    credentials: dict = Field(default_factory=dict)


class ShopOut(BaseModel):
    id: UUID
    marketplace: Marketplace
    name: str
    is_active: bool
    sync_enabled: bool
    last_sync_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


# --- Sync ---
class SyncSectionResult(BaseModel):
    status: str  # "success" | "error" | "skipped"
    count: int = 0
    message: Optional[str] = None


class ShopSyncResult(BaseModel):
    shop_id: str
    marketplace: str
    status: str
    message: Optional[str] = None
    orders: SyncSectionResult
    stocks: SyncSectionResult
    adverts: SyncSectionResult
    prices: SyncSectionResult
    finance: SyncSectionResult
    balance: SyncSectionResult


class BalanceOut(BaseModel):
    shop_id: UUID
    marketplace: Marketplace
    shop_name: str
    balance: Decimal | str
    payout_at: Optional[datetime]
    currency: str
    updated_at: Optional[datetime]


class SyncLogOut(BaseModel):
    id: UUID
    shop_id: UUID
    status: str
    sections: dict
    message: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


# --- Monthly targets / Plan-Fact ---
TARGET_METRICS = ("revenue", "profit", "margin", "drr", "orders", "returns", "stock")


class MonthlyTargetIn(BaseModel):
    targets: Dict[str, Optional[float]] = Field(default_factory=dict)

    @field_validator("targets")
    @classmethod
    def valid_target_keys(cls, values):
        unknown = set(values) - set(TARGET_METRICS)
        if unknown:
            raise ValueError(f"Unknown target metrics: {sorted(unknown)}")
        return values


class MonthlyTargetOut(BaseModel):
    id: UUID
    user_id: UUID
    month: date
    targets: Dict[str, Optional[float]]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# --- Dashboard ---
class MarketplaceKPI(BaseModel):
    marketplace: str
    revenue: Decimal
    actual_revenue: Decimal
    expenses: Decimal
    gross_profit: Decimal
    net_profit: Decimal
    drr: Decimal


class KPIData(BaseModel):
    revenue: Decimal
    actual_revenue: Decimal
    gross_profit: Decimal
    net_profit: Decimal
    drr: Decimal
    revenue_wow: float
    gross_wow: float
    net_wow: float
    drr_wow: float
    by_marketplace: List[MarketplaceKPI]
    revenue_trend: List[float] = []
    actual_revenue_trend: List[float] = []
    gross_trend: List[float] = []
    net_trend: List[float] = []
    drr_trend: List[float] = []


class MarketplaceComparison(BaseModel):
    marketplace: str
    revenue: Decimal
    expenses: Decimal
    gross_profit: Decimal
    net_profit: Decimal
    net_margin: Decimal
    drr: Decimal


class UnitEconomicsMarketplaceRow(BaseModel):
    marketplace: str
    sales: int
    gross_price: Decimal
    actual_price: Decimal
    cost: Decimal
    expense_per_unit: Decimal
    net_per_unit: Decimal
    margin: Decimal
    drr: Decimal
    trend: List[int] = []


class UnitEconomicsRow(BaseModel):
    sku: str
    name: str
    cost: Decimal
    rows: List[UnitEconomicsMarketplaceRow]


class ProductDashboardRow(BaseModel):
    sku: str
    name: str
    revenue: Decimal
    net_profit: Decimal
    margin: Decimal
    drr: Decimal
    avg_price: Decimal
    min_price: Decimal
    total_stock: int
    alert_price: bool
    alert_stock: bool


class AlertItem(BaseModel):
    type: str  # "danger" | "warning"
    text: str


class DailyTrendRow(BaseModel):
    date: date
    wb_revenue: Decimal
    ozon_revenue: Decimal
    ym_revenue: Decimal


class OrderStats(BaseModel):
    orders_count: int
    average_check: Decimal
    average_profit_per_order: Decimal
    profit_per_item: Decimal
    returns_count: int = 0
    return_rate: Decimal = Decimal("0")
    avg_items_per_order: Decimal = Decimal("0")
    orders_count_wow: float = 0.0
    average_check_wow: float = 0.0
    average_profit_per_order_wow: float = 0.0
    profit_per_item_wow: float = 0.0
    returns_count_wow: float = 0.0
    return_rate_wow: float = 0.0
    avg_items_per_order_wow: float = 0.0
    orders_count_trend: List[int] = []
    average_check_trend: List[float] = []
    average_profit_per_order_trend: List[float] = []
    profit_per_item_trend: List[float] = []
    returns_count_trend: List[int] = []
    avg_items_per_order_trend: List[float] = []


class CostCoverage(BaseModel):
    """How much of the period's sales are matched to a product with a cost price.

    Revenue without cost coverage means the profit for those sales is computed
    without deducting cost of goods — a reliability signal, not just a stat.
    """

    sales_total: int
    sales_with_cost: int
    revenue_covered: Decimal
    revenue_uncovered: Decimal
    coverage_percent: float


class DashboardData(BaseModel):
    kpi: KPIData
    order_stats: OrderStats
    alerts: List[AlertItem]
    marketplace_comparison: List[MarketplaceComparison]
    unit_economics: List[UnitEconomicsRow]
    products: List[ProductDashboardRow]
    daily_trend: List[DailyTrendRow] = []
    expense_structure: Dict[str, Decimal] = {}
    cost_coverage: Optional[CostCoverage] = None
    # "confirmed" when the period is older than the marketplace finance
    # confirmation delay; "estimated" for recent/ongoing periods.
    profit_status: str = "estimated"
