"""In-memory state store for OCTO products and availability."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from telemetry.setup import TelemetryInstruments

from models.product import Product

logger = logging.getLogger(__name__)


class StateManager:
    """Single source of truth for all OCTO product entities and availability."""

    def __init__(
        self,
        telemetry: "TelemetryInstruments | None" = None,
    ) -> None:
        self._products: dict[str, Product] = {}
        # {product_id: {option_id: [calendar_day_dicts]}}
        self._availability: dict[str, dict[str, list[dict]]] = {}
        self._tel = telemetry

    # ------------------------------------------------------------------
    # Public API — Products
    # ------------------------------------------------------------------

    def load_products(self, products: list[Product]) -> None:
        """Store products with uniqueness checks."""
        for product in products:
            self._validate_id_uniqueness(product)
            self._products[product.id] = product

        if self._tel:
            self._tel.server_products_count.add(len(self._products))

    def get_all_products(self) -> list[Product]:
        return list(self._products.values())

    def get_product(self, product_id: str) -> Optional[Product]:
        return self._products.get(product_id)

    # ------------------------------------------------------------------
    # Public API — Availability
    # ------------------------------------------------------------------

    def load_availability(
        self, availability: dict[str, dict[str, list[dict]]]
    ) -> None:
        """Store availability calendar data keyed by product_id → option_id."""
        self._availability.update(availability)
        total_days = sum(
            len(days)
            for options in availability.values()
            for days in options.values()
        )
        logger.info(
            "Availability loaded: %d products, %d total calendar days",
            len(availability), total_days,
        )

    def get_availability_calendar(
        self, product_id: str, option_id: str
    ) -> Optional[list[dict]]:
        """Return calendar days for a product+option, or None if not found."""
        product_avail = self._availability.get(product_id)
        if product_avail is None:
            return None
        return product_avail.get(option_id)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_id_uniqueness(product: Product) -> None:
        """Verify Option IDs unique within Product, Unit IDs unique within Option."""
        option_ids: set[str] = set()
        for option in product.options:
            if option.id in option_ids:
                raise ValueError(
                    f"Duplicate Option ID '{option.id}' in Product '{product.id}'"
                )
            option_ids.add(option.id)

            unit_ids: set[str] = set()
            for unit in option.units:
                if unit.id in unit_ids:
                    raise ValueError(
                        f"Duplicate Unit ID '{unit.id}' in Option '{option.id}'"
                    )
                unit_ids.add(unit.id)
