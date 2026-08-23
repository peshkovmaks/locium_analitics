from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List
from decimal import Decimal
from datetime import datetime
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


# --- Dashboard ---
class MarketplaceKPI(BaseModel):
    marketplace: str
    revenue: Decimal
    actual_revenue: Decimal
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
    price: Decimal
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


class DashboardData(BaseModel):
    kpi: KPIData
    alerts: List[AlertItem]
    marketplace_comparison: List[MarketplaceComparison]
    unit_economics: List[UnitEconomicsRow]
    products: List[ProductDashboardRow]
