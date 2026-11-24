"""The estimating pipeline: scope -> tasks -> takeoff, labor, sequence -> priced estimate."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .catalog import Catalog, load_catalog
from .labor import labor_by_trade
from .models import DOOR_BASE_LF, Room, parse_scope
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


def baseboard_note(room: Room) -> str:
    """Show the baseboard arithmetic so the deductions can be checked."""
    parts = []
    if room.doors:
        parts.append(f"{room.doors * DOOR_BASE_LF:g} lf for {room.doors} door opening"
                     f"{'s' if room.doors != 1 else ''} at {DOOR_BASE_LF:g} lf each")
    parts += [f"{lf:g} lf for {label}" for label, lf in room.base_deductions]
    less = (" less " + ", ".join(parts)) if parts else " with nothing deducted"
    return (f"{room.name}: baseboard is {room.baseboard_lf:g} lf: the {room.perimeter_lf:g} lf "
            f"perimeter{less}.")


def assumptions_for(scope: Any, catalog: Catalog) -> list[str]:
    """Catalog defaults that shaped this estimate, for the estimator to confirm."""
    notes: list[str] = []
    selected = {s.code for room in scope.rooms for s in room.selections}
    for item in catalog.items.values():
        if item.code in selected:
            notes += [note for note in item.notes if note not in notes]
    for room in scope.rooms:
        uses_geometry = any(
            catalog.items[s.code].basis == "baseboard_lf" and s.qty is None for s in room.selections)
        if uses_geometry:
            notes.append(baseboard_note(room))
    return notes


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
                "baseboard_lf": round(room.baseboard_lf, 2),
                "base_deductions": [{"label": label, "lf": lf} for label, lf in room.base_deductions],
                "labor_factor": room.labor_factor,
            }
            for room in scope.rooms
        ],
        "line_items": [
            {
                "room": task.room.name,
                "code": task.item.code,
                "stage": task.stage.key,
                "name": task.name,
                "trade": task.item.trade,
                "phase": task.phase,
                "phase_name": catalog.phase_name(task.phase),
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
                    "stage": step.task.stage.key,
                    "name": step.task.name,
                    "trade": step.task.item.trade,
                    "phase_name": catalog.phase_name(step.task.phase),
                    "start_day": step.start_day,
                    "end_day": step.end_day,
                    "hours": step.task.labor_hours,
                    "wait_days": step.task.wait_days,
                    "wait_for": step.task.stage.wait_for,
                }
                for step in steps
            ],
        },
        "by_trade": cost_by_trade(materials, labor_priced, trade_order),
        "totals": totals(materials, labor_priced, price_book),
        "warnings": warnings,
        "assumptions": assumptions_for(scope, catalog),
    }
    return _plain(result)
