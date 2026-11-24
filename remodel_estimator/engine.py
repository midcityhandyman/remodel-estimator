"""The estimating pipeline: scope -> tasks -> takeoff, labor, sequence -> priced estimate."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .catalog import Catalog, load_catalog
from .labor import labor_by_trade
from .models import parse_scope
from .pricebook import PriceBook, PriceBookError, load_price_book
from .pricing import cost_by_trade, price_labor, price_materials, totals
from .sequencing import build_schedule, missing_prerequisites, working_days
from .takeoff import build_tasks, material_takeoff


def _plain(value: Any) -> Any:
    """Convert Decimals to floats so the estimate is JSON serializable."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def estimate(scope_data: Any, catalog: Catalog | None = None,
             price_book: PriceBook | None = None) -> dict[str, Any]:
    """Build a full estimate from raw scope data (the parsed JSON of a scope file).

    Raises ScopeError for a bad scope and PriceBookError when the price book
    does not cover a needed SKU or trade.
    """
    catalog = catalog or load_catalog()
    price_book = price_book or load_price_book()
    scope = parse_scope(scope_data, catalog)
    trade_order = catalog.trades()

    tasks = build_tasks(scope, catalog)
    takeoff = material_takeoff(tasks, trade_order)
    gaps = price_book.missing({line.sku for line in takeoff}, {task.item.trade for task in tasks})
    if gaps:
        raise PriceBookError(f"price book {price_book.name!r} is missing: " + ", ".join(gaps))

    labor = labor_by_trade(tasks, price_book, trade_order)
    steps = build_schedule(tasks, catalog, price_book)
    materials = price_materials(takeoff, price_book)
    labor_priced = price_labor(labor, price_book)

    warnings = missing_prerequisites(tasks, catalog)
    for entry in labor:
        if entry.minimum_applied:
            warnings.append(
                f"{entry.trade}: {entry.hours:g} estimated hours raised to the "
                f"{entry.billable_hours:g} hour trade minimum."
            )
    if price_book.sample:
        warnings.append("Prices come from a sample price book and are not real. Replace it before quoting.")

    result = {
        "project": scope.project,
        "price_book": price_book.header(),
        "rooms": [
            {
                "name": room.name,
                "length_ft": room.length_ft,
                "width_ft": room.width_ft,
                "height_ft": room.height_ft,
                "floor_sf": round(room.floor_sf, 2),
                "wall_sf": round(room.wall_sf, 2),
                "ceiling_sf": round(room.ceiling_sf, 2),
                "perimeter_lf": round(room.perimeter_lf, 2),
                "labor_factor": room.labor_factor,
            }
            for room in scope.rooms
        ],
        "line_items": [
            {
                "room": task.room.name,
                "code": task.item.code,
                "name": task.item.name,
                "trade": task.item.trade,
                "phase": task.item.phase,
                "phase_name": catalog.phase_name(task.item.phase),
                "quantity": round(task.quantity, 2),
                "unit": task.item.unit,
                "labor_hours": task.labor_hours,
            }
            for task in tasks
        ],
        "takeoff": materials,
        "labor": labor_priced,
        "schedule": {
            "working_days": working_days(steps),
            "steps": [
                {
                    "room": step.task.room.name,
                    "code": step.task.item.code,
                    "name": step.task.item.name,
                    "trade": step.task.item.trade,
                    "phase_name": catalog.phase_name(step.task.item.phase),
                    "start_day": step.start_day,
                    "end_day": step.end_day,
                    "hours": step.task.labor_hours,
                    "wait_days": step.task.item.wait_days,
                }
                for step in steps
            ],
        },
        "by_trade": cost_by_trade(materials, labor_priced, trade_order),
        "totals": totals(materials, labor_priced, price_book),
        "warnings": warnings,
    }
    return _plain(result)
