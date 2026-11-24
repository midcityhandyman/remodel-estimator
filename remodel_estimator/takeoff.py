"""Turn a scope into tasks, then into a material takeoff by trade."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .catalog import GEOMETRY_BASES, Catalog, WorkItem
from .models import Room, Scope


@dataclass(frozen=True)
class Task:
    """One work item in one room, with its measured quantity and labor hours."""

    room_index: int
    room: Room
    item: WorkItem
    quantity: float
    labor_hours: float
    waste_override: float | None = None


@dataclass(frozen=True)
class TakeoffLine:
    trade: str
    sku: str
    net_qty: float  # purchase units needed with no waste
    raw_qty: float  # purchase units needed with waste, before rounding
    purchase_qty: int  # whole units to buy
    waste_pct: float  # blended waste across the items that use this SKU, in percent


def task_quantity(room: Room, item: WorkItem, qty: float | None) -> float:
    """An explicit qty wins; otherwise room geometry, or 1 for 'each' items."""
    if qty is not None:
        return qty
    if item.basis in GEOMETRY_BASES:
        return room.basis_quantity(item.basis)
    return 1.0


def build_tasks(scope: Scope, catalog: Catalog) -> list[Task]:
    tasks: list[Task] = []
    for index, room in enumerate(scope.rooms):
        for selection in room.selections:
            item = catalog.items[selection.code]
            quantity = task_quantity(room, item, selection.qty)
            hours = (item.setup_hours + quantity * item.hours_per_unit) * room.labor_factor
            tasks.append(Task(index, room, item, quantity, round(hours, 2), selection.waste))
    return tasks


def whole_units(raw_qty: float) -> int:
    """Round up to whole purchase units, tolerant of float noise like 44.00000000000001."""
    if raw_qty <= 0:
        return 0
    return max(1, math.ceil(raw_qty - 1e-9))


def material_takeoff(tasks: list[Task], trade_order: list[str] | None = None) -> list[TakeoffLine]:
    """Sum material needs per trade and SKU across all rooms, then round up once.

    Rounding after summing matters: two small rooms that each need 0.42 of a bag
    of thinset need one bag between them, not two.
    """
    net: dict[tuple[str, str], float] = {}
    raw: dict[tuple[str, str], float] = {}
    for task in tasks:
        for use in task.item.materials:
            waste = use.waste if task.waste_override is None else task.waste_override
            base = task.quantity * use.multiplier / use.coverage
            key = (task.item.trade, use.sku)
            net[key] = net.get(key, 0.0) + base
            raw[key] = raw.get(key, 0.0) + base * (1 + waste)
    rank = {trade: i for i, trade in enumerate(trade_order or [])}
    keys = sorted(net, key=lambda k: (rank.get(k[0], len(rank)), k[0], k[1]))
    # Waste percent comes from the unrounded sums, so a 5 percent factor on a
    # fraction of a gallon still reads 5.0 and not 4.8.
    return [
        TakeoffLine(key[0], key[1], round(net[key], 3), round(raw[key], 3), whole_units(raw[key]),
                    round((raw[key] / net[key] - 1) * 100, 1) if net[key] > 0 else 0.0)
        for key in keys
    ]
