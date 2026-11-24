"""Labor hours by trade, including each trade's minimum billable hours."""

from __future__ import annotations

from dataclasses import dataclass

from .pricebook import PriceBook
from .takeoff import Task


@dataclass(frozen=True)
class TradeLabor:
    trade: str
    hours: float  # estimated working hours
    billable_hours: float  # hours after the trade minimum is applied

    @property
    def minimum_applied(self) -> bool:
        return self.billable_hours > self.hours


def labor_by_trade(tasks: list[Task], price_book: PriceBook,
                   trade_order: list[str] | None = None) -> list[TradeLabor]:
    hours: dict[str, float] = {}
    for task in tasks:
        hours[task.item.trade] = hours.get(task.item.trade, 0.0) + task.labor_hours
    rank = {trade: i for i, trade in enumerate(trade_order or [])}
    result = []
    for trade in sorted(hours, key=lambda t: (rank.get(t, len(rank)), t)):
        worked = round(hours[trade], 2)
        billable = max(worked, price_book.rate(trade).min_hours)
        result.append(TradeLabor(trade, worked, round(billable, 2)))
    return result
