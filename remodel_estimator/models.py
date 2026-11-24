"""Scope input: rooms, dimensions and selected work items, with validation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .catalog import GEOMETRY_BASES, Catalog

DOOR_SF = 20.0  # opening deducted from wall area per door
WINDOW_SF = 12.0  # opening deducted from wall area per window
DOOR_BASE_LF = 3.0  # baseboard deducted per door opening (a 2 ft 8 in door plus casing)
MAX_BASE_DEDUCTIONS = 20
MAX_ROOMS = 50


class ScopeError(ValueError):
    """The scope is invalid. `problems` lists every issue found, not just the first."""

    def __init__(self, problems: list[str]):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


@dataclass(frozen=True)
class Selection:
    code: str
    qty: float | None = None  # overrides the computed or default quantity
    waste: float | None = None  # overrides the catalog waste factor for this item


@dataclass(frozen=True)
class Room:
    name: str
    length_ft: float
    width_ft: float
    height_ft: float = 8.0
    doors: int = 0
    windows: int = 0
    labor_factor: float = 1.0
    selections: tuple[Selection, ...] = ()
    # Wall runs at floor level that get no baseboard: (label, lf), for example
    # ("tub or shower", 10.0) or ("vanity", 3.0).
    base_deductions: tuple[tuple[str, float], ...] = ()

    @property
    def floor_sf(self) -> float:
        return self.length_ft * self.width_ft

    @property
    def ceiling_sf(self) -> float:
        return self.floor_sf

    @property
    def perimeter_lf(self) -> float:
        return 2 * (self.length_ft + self.width_ft)

    @property
    def baseboard_lf(self) -> float:
        """Perimeter less door openings and the listed no-baseboard runs, never below zero."""
        deducted = self.doors * DOOR_BASE_LF + sum(lf for _, lf in self.base_deductions)
        return max(0.0, self.perimeter_lf - deducted)

    @property
    def wall_sf(self) -> float:
        """Net wall area: gross minus door and window openings, never below zero."""
        gross = self.perimeter_lf * self.height_ft
        return max(0.0, gross - self.doors * DOOR_SF - self.windows * WINDOW_SF)

    def basis_quantity(self, basis: str) -> float:
        if basis not in GEOMETRY_BASES:
            raise KeyError(basis)
        return float(getattr(self, basis))


@dataclass(frozen=True)
class Scope:
    project: str
    rooms: tuple[Room, ...]


def _number(value: Any, label: str, problems: list[str], *, low: float, high: float,
            allow_low: bool = False) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        problems.append(f"{label} must be a number")
        return None
    too_low = value < low if allow_low else value <= low
    if too_low or value > high:
        bound = "at least" if allow_low else "greater than"
        problems.append(f"{label} must be {bound} {low:g} and at most {high:g}")
        return None
    return float(value)


def _count(value: Any, label: str, problems: list[str]) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 50:
        problems.append(f"{label} must be a whole number from 0 to 50")
        return 0
    return value


def _parse_selection(raw: Any, label: str, catalog: Catalog, problems: list[str]) -> Selection | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("code"), str):
        problems.append(f"{label} must be an object with a string 'code'")
        return None
    code = raw["code"]
    item = catalog.get(code)
    if item is None:
        problems.append(f"{label}: unknown work item code {code!r}")
        return None
    qty = raw.get("qty")
    if qty is not None:
        qty = _number(qty, f"{label} ({code}) qty", problems, low=0, high=100000)
    elif item.basis in ("lf", "sf"):
        problems.append(f"{label} ({code}) needs a qty in {item.unit}")
    waste = raw.get("waste")
    if waste is not None:
        waste = _number(waste, f"{label} ({code}) waste", problems, low=0, high=1, allow_low=True)
    return Selection(code=code, qty=qty, waste=waste)


def _parse_deductions(raw: Any, label: str, problems: list[str]) -> tuple[tuple[str, float], ...]:
    """Optional list of {"label": "vanity", "lf": 3} runs where no baseboard is installed."""
    if not isinstance(raw, list) or len(raw) > MAX_BASE_DEDUCTIONS:
        problems.append(f"{label} must be a list of at most {MAX_BASE_DEDUCTIONS} entries")
        return ()
    found = []
    for i, entry in enumerate(raw):
        where = f"{label}[{i}]"
        if not isinstance(entry, dict) or not isinstance(entry.get("label"), str) or not entry["label"].strip():
            problems.append(f"{where} must be an object with a 'label' and an 'lf'")
            continue
        lf = _number(entry.get("lf"), f"{where}.lf", problems, low=0, high=1000)
        if lf is not None:
            found.append((entry["label"].strip()[:40], lf))
    return tuple(found)


def _parse_room(raw: Any, index: int, catalog: Catalog, problems: list[str]) -> Room | None:
    label = f"rooms[{index}]"
    if not isinstance(raw, dict):
        problems.append(f"{label} must be an object")
        return None
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        problems.append(f"{label}.name is required")
        name = f"Room {index + 1}"
    before = len(problems)
    length = _number(raw.get("length_ft"), f"{label}.length_ft", problems, low=0, high=200)
    width = _number(raw.get("width_ft"), f"{label}.width_ft", problems, low=0, high=200)
    height = _number(raw.get("height_ft", 8), f"{label}.height_ft", problems, low=0, high=30)
    factor = _number(raw.get("labor_factor", 1.0), f"{label}.labor_factor", problems, low=0.5, high=3,
                     allow_low=True)
    doors = _count(raw.get("doors", 0), f"{label}.doors", problems)
    windows = _count(raw.get("windows", 0), f"{label}.windows", problems)

    deductions = _parse_deductions(raw.get("base_deductions", []), f"{label}.base_deductions", problems)

    raw_items = raw.get("work_items")
    selections: list[Selection] = []
    if not isinstance(raw_items, list) or not raw_items:
        problems.append(f"{label}.work_items must be a non-empty list")
    else:
        seen: set[str] = set()
        for i, raw_item in enumerate(raw_items):
            selection = _parse_selection(raw_item, f"{label}.work_items[{i}]", catalog, problems)
            if selection is None:
                continue
            if selection.code in seen:
                problems.append(f"{label}: work item {selection.code!r} is listed more than once")
            seen.add(selection.code)
            selections.append(selection)
    if len(problems) > before:
        return None
    return Room(name.strip(), length, width, height, doors, windows, factor, tuple(selections), deductions)


def parse_scope(data: Any, catalog: Catalog) -> Scope:
    """Validate raw JSON data and return a Scope, or raise ScopeError with every problem."""
    if not isinstance(data, dict):
        raise ScopeError(["scope must be a JSON object"])
    problems: list[str] = []
    project = data.get("project", "Untitled project")
    if not isinstance(project, str) or not project.strip():
        problems.append("project must be a non-empty string")
        project = "Untitled project"
    raw_rooms = data.get("rooms")
    rooms: list[Room] = []
    if not isinstance(raw_rooms, list) or not raw_rooms:
        problems.append("rooms must be a non-empty list")
    elif len(raw_rooms) > MAX_ROOMS:
        problems.append(f"at most {MAX_ROOMS} rooms are supported")
    else:
        for index, raw_room in enumerate(raw_rooms):
            room = _parse_room(raw_room, index, catalog, problems)
            if room is not None:
                rooms.append(room)
    if problems:
        raise ScopeError(problems)
    return Scope(project=project.strip(), rooms=tuple(rooms))
