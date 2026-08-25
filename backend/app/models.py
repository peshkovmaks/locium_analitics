import uuid
from datetime import datetime
from sqlalchemy import (
    Column,
    String,
    Integer,
    Boolean,
    DateTime,
    ForeignKey,
    Enum,
    Numeric,
    Text,
    JSON,
    UniqueConstraint,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.database import Base
import enum


class UserRole(str, enum.Enum):
    admin = "admin"
    viewer = "viewer"


class Marketplace(str, enum.Enum):
    wb = "wb"
    ozon = "ozon"
    yandex_market = "ym"


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(Enum(UserRole), default=UserRole.admin, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    shops = relationship("Shop", back_populates="user", cascade="all, delete-orphan")
    products = relationship(
        "Product", back_populates="user", cascade="all, delete-orphan"
    )


class Shop(Base):
    __tablename__ = "shops"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    marketplace = Column(Enum(Marketplace), nullable=False)
    name = Column(String(255), nullable=False)
    credentials = Column(JSONB, default={})  # Encrypted API keys
    is_active = Column(Boolean, default=True)
    sync_enabled = Column(Boolean, default=True)
    last_sync_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="shops")
    shop_products = relationship(
        "ShopProduct", back_populates="shop", cascade="all, delete-orphan"
    )
    sales = relationship("Sale", back_populates="shop", cascade="all, delete-orphan")
    stocks = relationship("Stock", back_populates="shop", cascade="all, delete-orphan")
    adverts = relationship(
        "Advert", back_populates="shop", cascade="all, delete-orphan"
    )
    sync_logs = relationship(
        "SyncLog", back_populates="shop", cascade="all, delete-orphan", order_by="desc(SyncLog.created_at)"
    )
    balance = relationship(
        "ShopBalance", back_populates="shop", uselist=False, cascade="all, delete-orphan"
    )


class SyncLog(Base):
    __tablename__ = "sync_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id = Column(
        UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status = Column(String(20), nullable=False, default="success")
    sections = Column(JSONB, default=dict)
    message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    shop = relationship("Shop", back_populates="sync_logs")


class Product(Base):
    __tablename__ = "products"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    sku = Column(String(100), nullable=False)
    canonical_sku = Column(String(100), nullable=True)
    name = Column(String(500), nullable=False)
    cost_price = Column(Numeric(12, 2), default=0)
    min_price = Column(Numeric(12, 2), default=0)
    weight_kg = Column(Numeric(8, 3), nullable=True)
    category = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="products")
    mappings = relationship(
        "ProductShopMapping", back_populates="product", cascade="all, delete-orphan"
    )
    shop_products = relationship(
        "ShopProduct", back_populates="product", cascade="all, delete-orphan"
    )


class ShopProduct(Base):
    __tablename__ = "shop_products"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id = Column(
        UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False
    )
    product_id = Column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )
    external_sku = Column(String(255), nullable=False)
    external_id = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True)

    shop = relationship("Shop", back_populates="shop_products")
    product = relationship("Product", back_populates="shop_products")


class Sale(Base):
    __tablename__ = "sales"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id = Column(
        UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False
    )
    date = Column(DateTime, nullable=False, index=True)
    external_sku = Column(String(255), nullable=False, index=True)
    external_id = Column(String(255), nullable=False)
    quantity = Column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint("shop_id", "external_id", "external_sku", name="uix_sale_order_sku"),
    )
    price = Column(Numeric(12, 2), default=0)
    customer_price = Column(Numeric(12, 2), default=0)
    marketplace_discount = Column(Numeric(12, 2), default=0)
    revenue = Column(Numeric(12, 2), default=0)
    commission = Column(Numeric(12, 2), default=0)
    logistics = Column(Numeric(12, 2), default=0)
    storage = Column(Numeric(12, 2), default=0)
    advertising = Column(Numeric(12, 2), default=0)
    returns = Column(Numeric(12, 2), default=0)
    insurance = Column(Numeric(12, 2), default=0)
    acquiring = Column(Numeric(12, 2), default=0)
    other = Column(Numeric(12, 2), default=0)
    is_return = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    shop = relationship("Shop", back_populates="sales")


class Stock(Base):
    __tablename__ = "stocks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id = Column(
        UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False
    )
    date = Column(DateTime, nullable=False, index=True)
    external_sku = Column(String(255), nullable=False, index=True)
    warehouse = Column(String(255), nullable=True)
    quantity = Column(Integer, default=0)
    in_way = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    shop = relationship("Shop", back_populates="stocks")


class Advert(Base):
    __tablename__ = "adverts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id = Column(
        UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), nullable=False
    )
    date = Column(DateTime, nullable=False, index=True)
    campaign_id = Column(String(255), nullable=True)
    external_sku = Column(String(255), nullable=False, index=True)
    views = Column(Integer, default=0)
    clicks = Column(Integer, default=0)
    ctr = Column(Numeric(5, 2), default=0)
    cpc = Column(Numeric(12, 2), default=0)
    spend = Column(Numeric(12, 2), default=0)
    orders = Column(Integer, default=0)
    cr = Column(Numeric(5, 2), default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    shop = relationship("Shop", back_populates="adverts")


class ShopBalance(Base):
    __tablename__ = "shop_balances"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id = Column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    balance = Column(Numeric(14, 2), default=0, nullable=False)
    payout_at = Column(DateTime, nullable=True)
    currency = Column(String(10), default="RUB", nullable=False)
    is_supported = Column(Boolean, default=True, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("shop_id", name="uix_shop_balance_shop_id"),
    )

    shop = relationship("Shop", back_populates="balance")


class ProductShopMapping(Base):
    __tablename__ = "product_shop_mappings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    shop_id = Column(UUID(as_uuid=True), ForeignKey("shops.id"), nullable=False)
    external_sku = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    product = relationship("Product", back_populates="mappings")
    shop = relationship("Shop")


class FinanceTransaction(Base):
    __tablename__ = "finance_transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id = Column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    marketplace = Column(Enum(Marketplace), nullable=False, index=True)
    operation_date = Column(DateTime, nullable=False, index=True)
    posting_number = Column(String(255), nullable=True, index=True)
    external_sku = Column(String(255), nullable=True, index=True)
    operation_type = Column(String(255), nullable=True)
    operation_name = Column(String(255), nullable=True)
    category = Column(String(50), nullable=False)
    amount = Column(Numeric(12, 2), default=0)
    raw_data = Column(JSONB, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_finance_transactions_shop_date_cat", "shop_id", "operation_date", "category"),
        Index("ix_finance_transactions_shop_sku_date", "shop_id", "external_sku", "operation_date"),
    )
