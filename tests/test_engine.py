import copy
from decimal import Decimal

import pytest

from remodel_estimator import PriceBookError, estimate
from remodel_estimator.labor import labor_by_trade
from remodel_estimator.models import parse_scope
from remodel_estimator.pricebook import price_book_from_dict
from remodel_estimator.pricing import money
from remodel_estimator.sequencing import build_schedule, duration_days
from remodel_estimator.takeoff import build_tasks, material_takeoff, whole_units

from .conftest import one_room


def takeoff_for(scope_data, catalog):
    tasks = build_tasks(parse_scope(scope_data, catalog), catalog)
    return {line.sku: line for line in material_takeoff(tasks, catalog.trades())}


def step_map(est):
    return {(s["room"], s["code"]): s for s in est["schedule"]["steps"]}


# ---- takeoff ---------------------------------------------------------------

def test_whole_units_rounds_up_and_ignores_float_noise():
    assert whole_units(0) == 0
    assert whole_units(0.01) == 1
    assert whole_units(3.2) == 4
    assert whole_units(40 * 1.10) == 44  # 44.00000000000001 in floating point


def test_tile_floor_takeoff_applies_waste(catalog):
    lines = takeoff_for(one_room([{"code": "tile_floor"}]), catalog)
    tile = lines["tile_floor_sf"]
    assert tile.net_qty == 40
    assert tile.waste_pct == 10.0
    assert tile.purchase_qty == 44
    assert lines["thinset_bag"].purchase_qty == 1  # 40 / 50 * 1.05 = 0.84
    assert lines["grout_bag"].purchase_qty == 1


def test_paint_takeoff_uses_coats_and_net_wall_area(catalog):
    lines = takeoff_for(one_room([{"code": "prime_paint_walls"}]), catalog)
    # 176 sf net wall: primer 176 / 300 * 1.05 = 0.62, paint two coats 176 * 2 / 350 * 1.05 = 1.06
    assert lines["primer_gal"].purchase_qty == 1
    assert lines["wall_paint_gal"].purchase_qty == 2
    assert lines["primer_gal"].waste_pct == 5.0  # from unrounded sums, not the rounded display values


def test_explicit_quantity_overrides_geometry(catalog):
    lines = takeoff_for(one_room([{"code": "tile_floor", "qty": 100}]), catalog)
    assert lines["tile_floor_sf"].purchase_qty == 110


def test_waste_override_per_item(catalog):
    lines = takeoff_for(one_room([{"code": "tile_floor", "waste": 0.2}]), catalog)
    assert lines["tile_floor_sf"].purchase_qty == 48
    assert lines["tile_floor_sf"].waste_pct == 20.0


def test_materials_are_summed_across_rooms_before_rounding(catalog):
    room = {"length_ft": 5, "width_ft": 4, "work_items": [{"code": "tile_floor"}]}
    scope = {"project": "Two small rooms", "rooms": [dict(room, name="A"), dict(room, name="B")]}
    lines = takeoff_for(scope, catalog)
    # Each 20 sf room needs 0.42 bag of thinset. Together that is one bag, not two.
    assert lines["thinset_bag"].purchase_qty == 1
    assert lines["tile_floor_sf"].purchase_qty == 44


def test_same_sku_from_two_items_in_one_trade_is_merged(catalog):
    scope = one_room([{"code": "tile_floor"}, {"code": "tile_shower_walls", "qty": 75},
                      {"code": "cement_board", "qty": 75}, {"code": "shower_waterproof", "qty": 75}])
    lines = takeoff_for(scope, catalog)
    # thinset: (40 + 75) / 50 * 1.05 = 2.415
    assert lines["thinset_bag"].purchase_qty == 3
    assert lines["waterproof_membrane_gal"].purchase_qty == 2  # 75 * 2 / 100 * 1.05 = 1.575


# ---- labor -----------------------------------------------------------------

def test_labor_hours_include_setup_and_room_factor(catalog):
    plain = build_tasks(parse_scope(one_room([{"code": "tile_floor"}]), catalog), catalog)
    assert plain[0].labor_hours == pytest.approx(1.0 + 40 * 0.16)
    hard = build_tasks(parse_scope(one_room([{"code": "tile_floor"}], labor_factor=1.5), catalog), catalog)
    assert hard[0].labor_hours == pytest.approx(7.4 * 1.5)


def test_trade_minimum_hours_are_billed(catalog, price_book):
    tasks = build_tasks(parse_scope(one_room([{"code": "insulate_walls", "qty": 40}]), catalog), catalog)
    (entry,) = labor_by_trade(tasks, price_book)
    assert entry.hours == pytest.approx(0.98)
    assert entry.billable_hours == 2
    assert entry.minimum_applied


# ---- sequencing ------------------------------------------------------------

def test_duration_rounds_up_to_half_days():
    assert duration_days(0.5, 8, 1) == 0.5
    assert duration_days(4, 8, 1) == 0.5
    assert duration_days(4.1, 8, 1) == 1.0
    assert duration_days(17.5, 8, 1) == 2.5
    assert duration_days(16, 8, 2) == 1.0


def test_phases_run_in_order_within_a_room(example):
    est = estimate(example("bathroom"))
    steps = step_map(est)
    room = "Hall bath"
    chain = ["demo_walls", "rough_plumbing", "insulate_walls", "shower_pan", "cement_board",
             "shower_waterproof", "tile_shower_walls", "prime_paint_walls", "tile_floor", "vanity",
             "set_toilet", "baseboard", "final_clean"]
    for earlier, later in zip(chain, chain[1:]):
        assert steps[(room, earlier)]["end_day"] <= steps[(room, later)]["start_day"], (earlier, later)


def test_cure_time_delays_the_next_phase(example):
    steps = step_map(estimate(example("bathroom")))
    waterproof = steps[("Hall bath", "shower_waterproof")]
    tile = steps[("Hall bath", "tile_shower_walls")]
    assert waterproof["wait_days"] == 1
    assert tile["start_day"] >= waterproof["end_day"] + 1


def test_a_trade_never_works_two_tasks_at_once(example):
    est = estimate(example("basement"))
    by_trade = {}
    for step in est["schedule"]["steps"]:
        by_trade.setdefault(step["trade"], []).append((step["start_day"], step["end_day"]))
    for trade, spans in by_trade.items():
        spans.sort()
        for (_, end), (start, _) in zip(spans, spans[1:]):
            assert end <= start, trade


def test_different_trades_overlap_in_the_same_phase(example):
    steps = step_map(estimate(example("bathroom")))
    assert steps[("Hall bath", "rough_plumbing")]["start_day"] == steps[("Hall bath", "rough_circuit")]["start_day"]


def test_working_days_cover_the_last_step_and_cure(catalog, price_book):
    scope = parse_scope(one_room([{"code": "tile_floor"}]), catalog)
    est = estimate(one_room([{"code": "tile_floor"}]), catalog, price_book)
    steps = build_schedule(build_tasks(scope, catalog), catalog, price_book)
    assert steps[0].end_day == 1.0  # 7.4 hours, crew of one
    assert est["schedule"]["working_days"] == 2  # one day of work plus one day of cure


def test_missing_prerequisite_becomes_a_warning(catalog, price_book):
    est = estimate(one_room([{"code": "tile_shower_walls", "qty": 60}]), catalog, price_book)
    text = " ".join(est["warnings"])
    assert "Cement backer board" in text
    assert "Roll on waterproofing membrane" in text


# ---- pricing ---------------------------------------------------------------

def test_money_rounds_half_up():
    assert money(Decimal("2.345")) == Decimal("2.35")
    assert money(1.005) == Decimal("1.01")
    assert money(10) == Decimal("10.00")


def test_single_item_estimate_matches_hand_calculation(catalog, price_book):
    est = estimate(one_room([{"code": "tile_floor"}]), catalog, price_book)
    t = est["totals"]
    # 44 sf tile at 6.40, one bag thinset at 29.00, one bag grout at 24.00
    assert t["materials"] == pytest.approx(44 * 6.40 + 29.00 + 24.00)
    assert t["material_tax"] == pytest.approx(26.77)  # 8 percent of 334.60, half up
    assert t["labor"] == pytest.approx(7.4 * 80.0)
    direct = 334.60 + 26.77 + 592.00
    assert t["direct_cost"] == pytest.approx(direct)
    assert t["overhead"] == pytest.approx(95.34)
    assert t["contingency"] == pytest.approx(47.67)
    assert t["profit"] == pytest.approx(109.64)  # 10 percent of 1096.38
    assert t["total"] == pytest.approx(direct + 95.34 + 47.67 + 109.64)


@pytest.mark.parametrize("name", ["bathroom", "kitchen", "basement"])
def test_example_totals_reconcile(example, name):
    est = estimate(example(name))
    t = est["totals"]
    assert t["materials"] == pytest.approx(sum(line["extended"] for line in est["takeoff"]))
    assert t["labor"] == pytest.approx(sum(entry["cost"] for entry in est["labor"]))
    assert t["materials"] + t["labor"] == pytest.approx(sum(row["total"] for row in est["by_trade"]))
    assert t["total"] == pytest.approx(
        t["direct_cost"] + t["overhead"] + t["contingency"] + t["profit"])
    assert sum(item["labor_hours"] for item in est["line_items"]) == pytest.approx(
        sum(entry["hours"] for entry in est["labor"]), abs=0.05)
    assert est["price_book"]["sample"] is True
    assert any("sample price book" in w for w in est["warnings"])


def test_estimate_is_deterministic(example):
    assert estimate(example("kitchen")) == estimate(example("kitchen"))


def test_markups_come_from_the_price_book(catalog, price_book_data):
    data = copy.deepcopy(price_book_data)
    data["sample"] = False
    data["markups"] = {"sales_tax_pct": 0, "overhead_pct": 0, "contingency_pct": 0, "profit_pct": 0}
    est = estimate(one_room([{"code": "tile_floor"}]), catalog, price_book_from_dict(data))
    assert est["totals"]["total"] == pytest.approx(est["totals"]["materials"] + est["totals"]["labor"])
    assert not any("sample price book" in w for w in est["warnings"])


def test_missing_price_is_an_error_not_a_zero(catalog, price_book_data):
    data = copy.deepcopy(price_book_data)
    del data["materials"]["grout_bag"]
    del data["labor"]["tile"]
    with pytest.raises(PriceBookError) as excinfo:
        estimate(one_room([{"code": "tile_floor"}]), catalog, price_book_from_dict(data))
    assert "SKU grout_bag" in str(excinfo.value)
    assert "trade tile" in str(excinfo.value)
