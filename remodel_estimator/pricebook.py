"""Price book: material unit prices, labor rates by trade and markups.

The bundled price book is a sample. Every number in it is invented.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from .paths import DEFAULT_PRICE_BOOK

PRICE_BOOK_ENV = "REMODEL_PRICE_BOOK"
MARKUP_KEYS = ("sales_tax_pct", "overhead_pct", "contingency_pct", "profit_pct")


class PriceBookError(ValueError):
    """The price book is malformed or does not cover what the estimate needs."""


@dataclass(frozen=True)
class MaterialPrice:
    sku: str
    description: str
    unit: str
    unit_price: Decimal


@dataclass(frozen=True)
class LaborRate:
    trade: str
    rate: Decimal
    min_hours: float
    crew_size: int


@dataclass(frozen=True)
class PriceBook:
    name: str
    sample: bool
    notice: str
    currency: str
    hours_per_day: float
    markups: dict[str, Decimal]
    labor: dict[str, LaborRate]
    materials: dict[str, MaterialPrice]

    def material(self, sku: str) -> MaterialPrice:
        try:
            return self.materials[sku]
        except KeyError:
            raise PriceBookError(f"price book {self.name!r} has no price for SKU {sku!r}") from None

    def rate(self, trade: str) -> LaborRate:
        try:
            return self.labor[trade]
        except KeyError:
            raise PriceBookError(f"price book {self.name!r} has no labor rate for trade {trade!r}") from None

    def missing(self, skus: set[str], trades: set[str]) -> list[str]:
        gaps = [f"SKU {s}" for s in sorted(skus - set(self.materials))]
        gaps += [f"trade {t}" for t in sorted(trades - set(self.labor))]
        return gaps

    def header(self) -> dict[str, Any]:
        return {"name": self.name, "sample": self.sample, "notice": self.notice, "currency": self.currency}


def _dec(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise PriceBookError(f"{label} must be a non-negative number")
    return Decimal(str(value))


def price_book_from_dict(data: dict[str, Any]) -> PriceBook:
    try:
        markups = {key: _dec(data["markups"].get(key, 0), f"markups.{key}") for key in MARKUP_KEYS}
        labor = {}
        for trade, raw in data["labor"].items():
            crew = int(raw.get("crew_size", 1))
            if crew < 1:
                raise PriceBookError(f"labor.{trade}.crew_size must be at least 1")
            labor[trade] = LaborRate(
                trade=trade,
                rate=_dec(raw["rate"], f"labor.{trade}.rate"),
                min_hours=float(_dec(raw.get("min_hours", 0), f"labor.{trade}.min_hours")),
                crew_size=crew,
            )
        materials = {
            sku: MaterialPrice(
                sku=sku,
                description=str(raw["description"]),
                unit=str(raw["unit"]),
                unit_price=_dec(raw["unit_price"], f"materials.{sku}.unit_price"),
            )
            for sku, raw in data["materials"].items()
        }
        hours_per_day = float(data.get("hours_per_day", 8))
        if hours_per_day <= 0:
            raise PriceBookError("hours_per_day must be positive")
        return PriceBook(
            name=str(data["name"]),
            sample=bool(data.get("sample", False)),
            notice=str(data.get("notice", "")),
            currency=str(data.get("currency", "USD")),
            hours_per_day=hours_per_day,
            markups=markups,
            labor=labor,
            materials=materials,
        )
    except (KeyError, TypeError, AttributeError) as exc:
        raise PriceBookError(f"price book is malformed: {exc!r}") from exc


def load_price_book(path: str | Path | None = None) -> PriceBook:
    """Load a price book. Order: explicit path, REMODEL_PRICE_BOOK, bundled sample."""
    chosen = path or os.environ.get(PRICE_BOOK_ENV) or DEFAULT_PRICE_BOOK
    with open(chosen, encoding="utf-8") as handle:
        return price_book_from_dict(json.load(handle))
