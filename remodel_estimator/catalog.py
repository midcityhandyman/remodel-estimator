"""Work item catalog: what each selectable item needs in materials and labor."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import DEFAULT_CATALOG

# basis -> display unit. Geometry bases are computed from room dimensions;
# manual bases need a quantity in the scope ("each" defaults to 1).
GEOMETRY_BASES = {"floor_sf": "sf", "wall_sf": "sf", "ceiling_sf": "sf", "perimeter_lf": "lf",
                  "baseboard_lf": "lf"}
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
class Stage:
    """One visit of a work item. Most items have a single stage."""

    key: str
    name: str
    phase: int
    hours_per_unit: float
    setup_hours: float
    wait_days: float
    wait_for: str  # what the wait is for: "cure" or "dry"
    materials: tuple[MaterialUse, ...]


@dataclass(frozen=True)
class WorkItem:
    code: str
    name: str
    trade: str
    basis: str
    requires: tuple[tuple[str, ...], ...]  # each entry is satisfied by any one of its codes
    stages: tuple[Stage, ...]
    notes: tuple[str, ...] = ()

    @property
    def unit(self) -> str:
        return BASIS_UNITS[self.basis]

    @property
    def phase(self) -> int:
        """Phase of the first visit."""
        return self.stages[0].phase

    @property
    def last_phase(self) -> int:
        return self.stages[-1].phase

    @property
    def hours_per_unit(self) -> float:
        return sum(stage.hours_per_unit for stage in self.stages)

    @property
    def setup_hours(self) -> float:
        return sum(stage.setup_hours for stage in self.stages)

    @property
    def materials(self) -> tuple[MaterialUse, ...]:
        return tuple(use for stage in self.stages for use in stage.materials)


class Catalog:
    def __init__(self, items: list[WorkItem], phases: dict[int, str],
                 phase_after: dict[int, tuple[int, ...]] | None = None):
        self.items: dict[str, WorkItem] = {}
        for item in items:
            if item.code in self.items:
                raise CatalogError(f"duplicate work item code: {item.code}")
            self.items[item.code] = item
        self.phases = phases
        self.phase_after = dict(phase_after or {})
        self.order = {code: index for index, code in enumerate(self.items)}
        self._check()
        self._blockers = {phase: self._find_blockers(phase) for phase in self.phases}

    def _check(self) -> None:
        for phase, after in self.phase_after.items():
            for earlier in after:
                if earlier not in self.phases or earlier >= phase:
                    raise CatalogError(f"phase {phase}: 'after' must name earlier phases, got {earlier}")
        for item in self.items.values():
            if item.basis not in BASIS_UNITS:
                raise CatalogError(f"{item.code}: unknown basis {item.basis!r}")
            if not item.stages:
                raise CatalogError(f"{item.code}: needs at least one stage")
            keys = [stage.key for stage in item.stages]
            if len(set(keys)) != len(keys):
                raise CatalogError(f"{item.code}: stage keys must be unique")
            for earlier, later in zip(item.stages, item.stages[1:]):
                if later.phase <= earlier.phase:
                    raise CatalogError(f"{item.code}: stages must be in strictly later phases, in order")
            for stage in item.stages:
                if stage.phase not in self.phases:
                    raise CatalogError(f"{item.code}: phase {stage.phase} has no name")
                if stage.hours_per_unit < 0 or stage.setup_hours < 0 or stage.wait_days < 0:
                    raise CatalogError(f"{item.code}: hours and wait days cannot be negative")
                for use in stage.materials:
                    if use.coverage <= 0 or use.multiplier <= 0:
                        raise CatalogError(f"{item.code}: coverage and multiplier must be positive ({use.sku})")
                    if not 0 <= use.waste <= 1:
                        raise CatalogError(f"{item.code}: waste must be between 0 and 1 ({use.sku})")
            for options in item.requires:
                if not options:
                    raise CatalogError(f"{item.code}: an empty 'requires' entry")
                for needed in options:
                    if needed not in self.items:
                        raise CatalogError(f"{item.code}: requires unknown item {needed!r}")
                    if self.items[needed].phase > item.last_phase:
                        raise CatalogError(f"{item.code}: requires {needed}, which is in a later phase")

    def _find_blockers(self, phase: int) -> frozenset[int]:
        """Phases that must be done (and cured) in a room before this phase starts there.

        By default that is every earlier phase. A phase with an explicit 'after'
        list waits only for those phases and whatever they wait for, which lets
        two tracks overlap (shower tile while the drywall coats dry).
        """
        if phase not in self.phase_after:
            return frozenset(p for p in self.phases if p < phase)
        found: set[int] = set()
        for earlier in self.phase_after[phase]:
            found.add(earlier)
            found |= self._find_blockers(earlier)
        return frozenset(found)

    def blockers(self, phase: int) -> frozenset[int]:
        return self._blockers[phase]

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
                    "requires": [list(options) if len(options) > 1 else options[0] for options in i.requires],
                    "stages": [{"key": s.key, "name": s.name, "phase": s.phase} for s in i.stages],
                }
                for i in self.items.values()
            ],
        }


def _materials(raw: list[dict[str, Any]]) -> tuple[MaterialUse, ...]:
    return tuple(
        MaterialUse(
            sku=m["sku"],
            coverage=float(m["coverage"]),
            waste=float(m.get("waste", 0)),
            multiplier=float(m.get("multiplier", 1)),
        )
        for m in raw
    )


def _stage(raw: dict[str, Any], key: str, name: str) -> Stage:
    return Stage(
        key=key,
        name=name,
        phase=int(raw["phase"]),
        hours_per_unit=float(raw["hours_per_unit"]),
        setup_hours=float(raw.get("setup_hours", 0)),
        wait_days=float(raw.get("wait_days", 0)),
        wait_for=str(raw.get("wait_for", "cure")),
        materials=_materials(raw.get("materials", [])),
    )


def catalog_from_dict(data: dict[str, Any]) -> Catalog:
    try:
        phases: dict[int, str] = {}
        phase_after: dict[int, tuple[int, ...]] = {}
        for key, value in data["phases"].items():
            if isinstance(value, dict):
                phases[int(key)] = str(value["name"])
                if "after" in value:
                    phase_after[int(key)] = tuple(int(p) for p in value["after"])
            else:
                phases[int(key)] = str(value)
        items = []
        for raw in data["items"]:
            if "stages" in raw:
                stages = tuple(_stage(s, str(s["key"]), str(s["name"])) for s in raw["stages"])
            else:
                stages = (_stage(raw, "main", raw["name"]),)
            note = raw.get("note", [])
            items.append(
                WorkItem(
                    code=raw["code"],
                    name=raw["name"],
                    trade=raw["trade"],
                    basis=raw["basis"],
                    requires=tuple(
                        (needed,) if isinstance(needed, str) else tuple(needed)
                        for needed in raw.get("requires", [])
                    ),
                    stages=stages,
                    notes=(note,) if isinstance(note, str) else tuple(note),
                )
            )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise CatalogError(f"catalog is malformed: {exc!r}") from exc
    return Catalog(items, phases, phase_after)


def load_catalog(path: str | Path | None = None) -> Catalog:
    with open(path or DEFAULT_CATALOG, encoding="utf-8") as handle:
        return catalog_from_dict(json.load(handle))
