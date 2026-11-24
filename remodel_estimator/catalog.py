"""Work item catalog: what each selectable item needs in materials and labor."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import DEFAULT_CATALOG

# basis -> display unit. Geometry bases are computed from room dimensions;
# manual bases need a quantity in the scope ("each" defaults to 1).
GEOMETRY_BASES = {"floor_sf": "sf", "wall_sf": "sf", "ceiling_sf": "sf", "perimeter_lf": "lf"}
MANUAL_BASES = {"each": "ea", "lf": "lf", "sf": "sf"}
BASIS_UNITS = {**GEOMETRY_BASES, **MANUAL_BASES}


class CatalogError(ValueError):
    """The catalog file is malformed."""


@dataclass(frozen=True)
class MaterialUse:
    sku: str
    coverage: float  # basis units covered by one purchase unit
    waste: float  # 0.10 means buy 10 percent extra
    multiplier: float = 1.0  # coats, layers


@dataclass(frozen=True)
class WorkItem:
    code: str
    name: str
    trade: str
    phase: int
    basis: str
    hours_per_unit: float
    setup_hours: float
    wait_days: float
    requires: tuple[str, ...]
    materials: tuple[MaterialUse, ...]

    @property
    def unit(self) -> str:
        return BASIS_UNITS[self.basis]


class Catalog:
    def __init__(self, items: list[WorkItem], phases: dict[int, str]):
        self.items: dict[str, WorkItem] = {}
        for item in items:
            if item.code in self.items:
                raise CatalogError(f"duplicate work item code: {item.code}")
            self.items[item.code] = item
        self.phases = phases
        self.order = {code: index for index, code in enumerate(self.items)}
        self._check()

    def _check(self) -> None:
        for item in self.items.values():
            if item.basis not in BASIS_UNITS:
                raise CatalogError(f"{item.code}: unknown basis {item.basis!r}")
            if item.phase not in self.phases:
                raise CatalogError(f"{item.code}: phase {item.phase} has no name")
            if item.hours_per_unit < 0 or item.setup_hours < 0 or item.wait_days < 0:
                raise CatalogError(f"{item.code}: hours and wait days cannot be negative")
            for needed in item.requires:
                if needed not in self.items:
                    raise CatalogError(f"{item.code}: requires unknown item {needed!r}")
                if self.items[needed].phase > item.phase:
                    raise CatalogError(f"{item.code}: requires {needed}, which is in a later phase")
            for use in item.materials:
                if use.coverage <= 0 or use.multiplier <= 0:
                    raise CatalogError(f"{item.code}: coverage and multiplier must be positive ({use.sku})")
                if not 0 <= use.waste <= 1:
                    raise CatalogError(f"{item.code}: waste must be between 0 and 1 ({use.sku})")

    def get(self, code: str) -> WorkItem | None:
        return self.items.get(code)

    def phase_name(self, phase: int) -> str:
        return self.phases[phase]

    def trades(self) -> list[str]:
        """Trades in the order they first show up on a job."""
        seen: list[str] = []
        for item in sorted(self.items.values(), key=lambda i: (i.phase, self.order[i.code])):
            if item.trade not in seen:
                seen.append(item.trade)
        return seen

    def skus(self) -> set[str]:
        return {use.sku for item in self.items.values() for use in item.materials}

    def to_public(self) -> dict[str, Any]:
        """Shape served to the web UI."""
        return {
            "phases": [{"phase": p, "name": n} for p, n in sorted(self.phases.items())],
            "items": [
                {
                    "code": i.code,
                    "name": i.name,
                    "trade": i.trade,
                    "phase": i.phase,
                    "basis": i.basis,
                    "unit": i.unit,
                    "needs_qty": i.basis in ("lf", "sf"),
                    "requires": list(i.requires),
                }
                for i in self.items.values()
            ],
        }


def catalog_from_dict(data: dict[str, Any]) -> Catalog:
    try:
        phases = {int(k): str(v) for k, v in data["phases"].items()}
        items = []
        for raw in data["items"]:
            materials = tuple(
                MaterialUse(
                    sku=m["sku"],
                    coverage=float(m["coverage"]),
                    waste=float(m.get("waste", 0)),
                    multiplier=float(m.get("multiplier", 1)),
                )
                for m in raw.get("materials", [])
            )
            items.append(
                WorkItem(
                    code=raw["code"],
                    name=raw["name"],
                    trade=raw["trade"],
                    phase=int(raw["phase"]),
                    basis=raw["basis"],
                    hours_per_unit=float(raw["hours_per_unit"]),
                    setup_hours=float(raw.get("setup_hours", 0)),
                    wait_days=float(raw.get("wait_days", 0)),
                    requires=tuple(raw.get("requires", [])),
                    materials=materials,
                )
            )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise CatalogError(f"catalog is malformed: {exc!r}") from exc
    return Catalog(items, phases)


def load_catalog(path: str | Path | None = None) -> Catalog:
    with open(path or DEFAULT_CATALOG, encoding="utf-8") as handle:
        return catalog_from_dict(json.load(handle))
