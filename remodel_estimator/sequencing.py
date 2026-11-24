"""Sequence tasks into a working day schedule.

Rules, kept deliberately simple:
- Within a room, a task waits for every task in an earlier phase, plus that
  task's cure or dry time (wait_days).
- A phase can name the phases it follows instead ('after' in the catalog).
  Waterproofing and wall tile follow the board hanging, not the drywall
  finishing, so the shower is tiled while the drywall coats dry.
- A staged item (drywall coats, electrical rough-in and trim-out) is one task
  per stage, each in its own phase.
- Each trade has one crew, so a trade cannot work two tasks at the same time.
  Different trades can overlap, in the same phase or in different rooms.
- Everything one trade does in one room in one phase is a single visit. The
  visit is rounded up to half days and its tasks run back to back inside it.
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
        return self.end_day + self.task.wait_days


def duration_days(hours: float, hours_per_day: float, crew_size: int) -> float:
    half_days = math.ceil(hours / (hours_per_day * crew_size) * 2 - 1e-9)
    return max(1, half_days) / 2


def build_schedule(tasks: list[Task], catalog: Catalog, price_book: PriceBook) -> list[Step]:
    # A visit is everything one trade does in one room in one phase. Its hours
    # are added up before rounding to half days, so three short demolition
    # tasks are one morning, not three half days.
    visits: dict[tuple[int, int, str], list[Task]] = {}
    for task in sorted(tasks, key=lambda t: (t.phase, t.room_index, catalog.order[t.item.code])):
        visits.setdefault((task.phase, task.room_index, task.item.trade), []).append(task)

    trade_free: dict[str, float] = {}
    room_ready: dict[int, dict[int, float]] = {}  # room -> phase -> day that phase is done and cured
    steps: list[Step] = []
    for (phase, room_index, trade), visit in visits.items():
        phases_done = room_ready.setdefault(room_index, {})
        blockers = catalog.blockers(phase)
        after_room = max((day for done, day in phases_done.items() if done in blockers), default=0.0)
        start = max(after_room, trade_free.get(trade, 0.0))
        hours = sum(task.labor_hours for task in visit)
        length = duration_days(hours, price_book.hours_per_day, price_book.rate(trade).crew_size)
        # Tasks inside the visit run back to back, each with its share of the time.
        worked = 0.0
        begin = start
        for index, task in enumerate(visit):
            worked += task.labor_hours
            last = index == len(visit) - 1
            share = worked / hours if hours > 0 else (index + 1) / len(visit)
            end = start + length if last else round(start + length * share, 2)
            step = Step(task, begin, end)
            begin = end
            phases_done[phase] = max(phases_done.get(phase, 0.0), step.ready_day)
            steps.append(step)
        trade_free[trade] = start + length
    return sorted(steps, key=lambda s: (s.start_day, s.task.phase, s.task.room_index,
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
        if task.stage is not task.item.stages[0]:
            continue  # one warning per selected item, not per visit
        for options in task.item.requires:
            if by_room[task.room_index].isdisjoint(options):
                names = " or ".join(f"'{catalog.items[code].name}'" for code in options)
                missing = "which is" if len(options) == 1 else "neither of which is"
                warnings.append(
                    f"{task.room.name}: '{task.item.name}' normally follows {names}, "
                    f"{missing} not in this room's scope."
                )
    return warnings
