"""Price a takeoff and labor summary from a price book. Money is Decimal throughout."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from .labor import TradeLabor
from .pricebook import PriceBook
from .takeoff import TakeoffLine

CENT = Decimal("0.01")


def money(value: Decimal | float | int) -> Decimal:
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def pct(amount: Decimal, percent: Decimal) -> Decimal:
    return money(amount * percent / Decimal(100))


def price_materials(lines: list[TakeoffLine], price_book: PriceBook) -> list[dict[str, Any]]:
    priced = []
    for line in lines:
        material = price_book.material(line.sku)
        priced.append({
            "trade": line.trade,
            "sku": line.sku,
            "description": material.description,
            "unit": material.unit,
            "net_qty": line.net_qty,
            "waste_pct": line.waste_pct,
            "purchase_qty": line.purchase_qty,
            "unit_price": money(material.unit_price),
            "extended": money(material.unit_price * line.purchase_qty),
        })
    return priced


def price_labor(labor: list[TradeLabor], price_book: PriceBook) -> list[dict[str, Any]]:
    priced = []
    for entry in labor:
        rate = price_book.rate(entry.trade)
        priced.append({
            "trade": entry.trade,
            "hours": entry.hours,
            "billable_hours": entry.billable_hours,
            "minimum_applied": entry.minimum_applied,
            "rate": money(rate.rate),
            "cost": money(rate.rate * Decimal(str(entry.billable_hours))),
        })
    return priced


def totals(materials: list[dict[str, Any]], labor: list[dict[str, Any]],
           price_book: PriceBook) -> dict[str, Decimal]:
    """Roll up: materials + tax + labor = direct cost; overhead and contingency on
    direct cost; profit on everything before it."""
    markups = price_book.markups
    material_total = sum((m["extended"] for m in materials), Decimal(0))
    labor_total = sum((entry["cost"] for entry in labor), Decimal(0))
    tax = pct(material_total, markups["sales_tax_pct"])
    direct = material_total + tax + labor_total
    overhead = pct(direct, markups["overhead_pct"])
    contingency = pct(direct, markups["contingency_pct"])
    profit = pct(direct + overhead + contingency, markups["profit_pct"])
    return {
        "materials": money(material_total),
        "material_tax": tax,
        "labor": money(labor_total),
        "direct_cost": money(direct),
        "overhead": overhead,
        "contingency": contingency,
        "profit": profit,
        "total": money(direct + overhead + contingency + profit),
    }


def cost_by_trade(materials: list[dict[str, Any]], labor: list[dict[str, Any]],
                  trade_order: list[str]) -> list[dict[str, Any]]:
    """Direct cost per trade before tax and markups."""
    rows: dict[str, dict[str, Decimal]] = {}
    for m in materials:
        rows.setdefault(m["trade"], {"materials": Decimal(0), "labor": Decimal(0)})["materials"] += m["extended"]
    for entry in labor:
        rows.setdefault(entry["trade"], {"materials": Decimal(0), "labor": Decimal(0)})["labor"] += entry["cost"]
    rank = {trade: i for i, trade in enumerate(trade_order)}
    return [
        {"trade": trade, "materials": money(row["materials"]), "labor": money(row["labor"]),
         "total": money(row["materials"] + row["labor"])}
        for trade, row in sorted(rows.items(), key=lambda kv: (rank.get(kv[0], len(rank)), kv[0]))
    ]
