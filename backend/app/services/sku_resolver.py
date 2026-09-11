"""Canonical SKU resolution: match a sale's external SKU to the right Product.

Single source of truth for product matching, used by the dashboard, reports
and the Telegram bot. Reproduces the matching rules of
``SyncService._ensure_products``:

1. ``ProductShopMapping`` by (shop_id, external_sku) — this survives product
   merges, because merge reassigns all mappings to the canonical product.
2. Fallback: direct match on ``Product.canonical_sku`` / ``Product.sku``.

The resolver is built from two bulk queries (products + mappings with the
product eagerly loaded), so per-sale lookups are plain dict hits and do not
generate extra SQL (no N+1).
"""

from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Product, ProductShopMapping, Shop


class SkuResolver:
    """Resolve (shop_id, external_sku) to the canonical Product."""

    def __init__(
        self,
        products: Iterable[Product],
        mappings: Iterable[ProductShopMapping],
    ):
        self._by_sku: Dict[str, Product] = {}
        for product in products:
            self._by_sku[product.sku] = product
            if product.canonical_sku and product.canonical_sku != product.sku:
                self._by_sku.setdefault(product.canonical_sku, product)
        self._by_shop_sku: Dict[Tuple[str, str], Product] = {}
        for mapping in mappings:
            if mapping.product is not None:
                self._by_shop_sku[(str(mapping.shop_id), mapping.external_sku)] = (
                    mapping.product
                )

    @classmethod
    async def create(
        cls,
        db: AsyncSession,
        user_id,
        shop_ids: Optional[List] = None,
    ) -> "SkuResolver":
        """Build a resolver from the DB in two queries.

        ``shop_ids`` restricts mappings to the shops in scope; when omitted,
        mappings of all the user's shops are loaded.
        """
        products_result = await db.execute(
            select(Product).where(Product.user_id == user_id)
        )
        products = products_result.scalars().all()

        mapping_query = select(ProductShopMapping).options(
            selectinload(ProductShopMapping.product)
        )
        if shop_ids is not None:
            mapping_query = mapping_query.where(
                ProductShopMapping.shop_id.in_(shop_ids)
            )
        else:
            mapping_query = mapping_query.join(
                Shop, ProductShopMapping.shop_id == Shop.id
            ).where(Shop.user_id == user_id)
        mappings_result = await db.execute(mapping_query)
        mappings = mappings_result.scalars().all()

        return cls(products, mappings)

    def resolve(self, shop_id, external_sku) -> Optional[Product]:
        """Canonical product for a sale's (shop_id, external_sku), if any."""
        if not external_sku:
            return None
        product = self._by_shop_sku.get((str(shop_id), external_sku))
        if product is not None:
            return product
        return self._by_sku.get(external_sku)

    def resolve_sku(self, sku) -> Optional[Product]:
        """Product by bare SKU/canonical SKU (no shop context)."""
        if not sku:
            return None
        return self._by_sku.get(sku)

    @property
    def products(self) -> List[Product]:
        """Unique products known to the resolver."""
        seen: Dict[str, Product] = {}
        for product in self._by_sku.values():
            seen[str(product.id)] = product
        return list(seen.values())
