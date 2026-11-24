"""Sequence tasks into a working day schedule.

Rules, kept deliberately simple:
- Within a room, a task waits for every task in an earlier phase, plus that
  task's cure or dry time (wait_days).
- Each trade has one crew, so a trade cannot work two tasks at the same time.
  Different trades can overlap, in the same phase or in different rooms.
- Durations are rounded up to half days.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .catalog import Catalog
from .pricebook import PriceBook
from .takeoff import Task


@dataclass(frozen=True)
class Step:
    task: Task
    start_day: float  # 0.0 is the morning of working day 1
    end_day: float

    @property
    def ready_day(self) -> float:
        """When later phases in the same room may start."""
        return self.end_day + self.task.item.wait_days


def duration_days(hours: float, hours_per_day: float, crew_size: int) -> float:
    half_days = math.ceil(hours / (hours_per_day * crew_size) * 2 - 1e-9)
    return max(1, half_days) / 2


def build_schedule(tasks: list[Task], catalog: Catalog, price_book: PriceBook) -> list[Step]:
    ordered = sorted(tasks, key=lambda t: (t.item.phase, t.room_index, catalog.order[t.item.code]))
    trade_free: dict[str, float] = {}
    room_ready: dict[int, dict[int, float]] = {}  # room -> phase -> day that phase is done and cured
    steps: list[Step] = []
    for task in ordered:
        phases_done = room_ready.setdefault(task.room_index, {})
        after_room = max((day for phase, day in phases_done.items() if phase < task.item.phase), default=0.0)
        start = max(after_room, trade_free.get(task.item.trade, 0.0))
        crew = price_book.rate(task.item.trade).crew_size
        step = Step(task, start, start + duration_days(task.labor_hours, price_book.hours_per_day, crew))
        trade_free[task.item.trade] = step.end_day
        phases_done[task.item.phase] = max(phases_done.get(task.item.phase, 0.0), step.ready_day)
        steps.append(step)
    return sorted(steps, key=lambda s: (s.start_day, s.task.item.phase, s.task.room_index,
                                        catalog.order[s.task.item.code]))


def working_days(steps: list[Step]) -> int:
    """Whole working days from start to the last task being done and cured."""
    return math.ceil(max((step.ready_day for step in steps), default=0.0))


def missing_prerequisites(tasks: list[Task], catalog: Catalog) -> list[str]:
    """Warn when a selected item normally follows another item that was not selected."""
    warnings = []
    by_room: dict[int, set[str]] = {}
    for task in tasks:
        by_room.setdefault(task.room_index, set()).add(task.item.code)
    for task in tasks:
        for needed in task.item.requires:
            if needed not in by_room[task.room_index]:
                warnings.append(
                    f"{task.room.name}: '{task.item.name}' normally follows "
                    f"'{catalog.items[needed].name}', which is not in this room's scope."
                )
    return warnings
