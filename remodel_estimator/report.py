"""Plain text rendering of an estimate for the CLI and the demo."""

from __future__ import annotations

import textwrap
from typing import Any

WIDTH = 96


def _usd(value: float) -> str:
    return f"${value:,.2f}"


def _day(value: float) -> str:
    """Schedule position in working days elapsed since the job started."""
    return f"{value:g}"


def render_text(est: dict[str, Any]) -> str:
    book = est["price_book"]
    out: list[str] = []
    rule = "=" * WIDTH
    out += [rule, f"ESTIMATE: {est['project']}"]
    tag = "SAMPLE PRICES, NOT REAL" if book["sample"] else "custom price book"
    out += [f"Price book: {book['name']} ({tag})", rule, ""]

    out.append("ROOMS")
    for room in est["rooms"]:
        out.append(
            f"  {room['name']:<16} {room['length_ft']:g} x {room['width_ft']:g} x {room['height_ft']:g} ft"
            f"   floor {room['floor_sf']:g} sf   walls {room['wall_sf']:g} sf   perimeter {room['perimeter_lf']:g} lf"
            f"   baseboard {room['baseboard_lf']:g} lf"
        )

    out += ["", "SCOPE LINE ITEMS"]
    out.append(f"  {'room':<12} {'work item':<38} {'trade':<11} {'qty':>8} {'unit':<4} {'hours':>7}")
    for item in est["line_items"]:
        out.append(
            f"  {item['room'][:12]:<12} {item['name'][:38]:<38} {item['trade']:<11} "
            f"{item['quantity']:>8g} {item['unit']:<4} {item['labor_hours']:>7.2f}"
        )

    out += ["", "MATERIAL TAKEOFF BY TRADE (waste included, rounded up to whole purchase units)"]
    current = None
    for line in est["takeoff"]:
        if line["trade"] != current:
            current = line["trade"]
            out.append(f"  [{current}]")
            out.append(f"    {'material':<52} {'net':>7} {'waste':>6} {'buy':>5} {'unit':<7} {'each':>9} {'extended':>11}")
        out.append(
            f"    {line['description'][:52]:<52} {line['net_qty']:>7.2f} {line['waste_pct']:>5.1f}% "
            f"{line['purchase_qty']:>5d} {line['unit']:<7} {_usd(line['unit_price']):>9} {_usd(line['extended']):>11}"
        )

    out += ["", "LABOR BY TRADE"]
    out.append(f"  {'trade':<12} {'hours':>8} {'billable':>9} {'rate':>9} {'cost':>12}")
    for entry in est["labor"]:
        flag = "  (trade minimum applied)" if entry["minimum_applied"] else ""
        out.append(
            f"  {entry['trade']:<12} {entry['hours']:>8.2f} {entry['billable_hours']:>9.2f} "
            f"{_usd(entry['rate']):>9} {_usd(entry['cost']):>12}{flag}"
        )

    schedule = est["schedule"]
    out += ["", f"SEQUENCE ({schedule['working_days']} working days, one crew per trade; "
                 "start and end are working days elapsed since the job started)"]
    out.append(f"  {'start':>6} {'end':>6}  {'room':<12} {'work item':<38} {'trade':<11} {'note'}")
    for step in schedule["steps"]:
        note = f"then wait {step['wait_days']:g} day to {step['wait_for']}" if step["wait_days"] else ""
        out.append(
            f"  {_day(step['start_day']):>6} {_day(step['end_day']):>6}  {step['room'][:12]:<12} "
            f"{step['name'][:38]:<38} {step['trade']:<11} {note}".rstrip()
        )

    out += ["", "DIRECT COST BY TRADE (before tax and markups)"]
    out.append(f"  {'trade':<12} {'materials':>12} {'labor':>12} {'total':>12}")
    for row in est["by_trade"]:
        out.append(f"  {row['trade']:<12} {_usd(row['materials']):>12} {_usd(row['labor']):>12} {_usd(row['total']):>12}")

    t = est["totals"]
    out += ["", "TOTALS"]
    for label, key in (
        ("Materials", "materials"), ("Sales tax on materials", "material_tax"), ("Labor", "labor"),
        ("Direct cost", "direct_cost"), ("Overhead", "overhead"), ("Contingency", "contingency"),
        ("Profit", "profit"),
    ):
        out.append(f"  {label:<26} {_usd(t[key]):>14}")
    out.append(f"  {'-' * 41}")
    out.append(f"  {'ESTIMATE TOTAL':<26} {_usd(t['total']):>14}")

    if est["warnings"]:
        out += ["", "NOTES"]
        out += [f"  * {warning}" for warning in est["warnings"]]
    if est["assumptions"]:
        out += ["", "ASSUMPTIONS (catalog defaults used in this estimate; check them against how you build)"]
        for note in est["assumptions"]:
            out += textwrap.wrap(note, WIDTH, initial_indent="  * ", subsequent_indent="    ")
    out.append("")
    return "\n".join(out)
