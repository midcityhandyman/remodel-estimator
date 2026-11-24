"""Construction logic: substrates, rough and finish visits, drywall coats, baseboard
deductions, fastener coverage and the sample production rates."""

import copy
import json

import pytest

from remodel_estimator import estimate
from remodel_estimator.catalog import CatalogError, catalog_from_dict
from remodel_estimator.models import Room, ScopeError, parse_scope
from remodel_estimator.paths import DEFAULT_CATALOG, REPO_ROOT
from remodel_estimator.report import render_text
from remodel_estimator.takeoff import build_tasks, material_takeoff

from .conftest import one_room


def takeoff_for(scope_data, catalog):
    tasks = build_tasks(parse_scope(scope_data, catalog), catalog)
    return {line.sku: line for line in material_takeoff(tasks, catalog.trades())}


def stage_steps(est, room=None):
    """Schedule steps keyed by (code, stage)."""
    return {(s["code"], s["stage"]): s for s in est["schedule"]["steps"] if room in (None, s["room"])}


def hours_by_stage(est):
    return {(i["code"], i["stage"]): i["labor_hours"] for i in est["line_items"]}


# ---- 1. floor tile substrate -------------------------------------------------

def test_floor_backer_board_takeoff_and_labor(catalog, price_book):
    scope = one_room([{"code": "floor_backer_board"}])
    lines = takeoff_for(scope, catalog)
    assert lines["cement_board_quarter_3x5"].purchase_qty == 3  # 40 / 15 * 1.10 = 2.93
    assert lines["cement_board_quarter_3x5"].waste_pct == 10.0
    assert lines["thinset_bag"].purchase_qty == 1  # bedding coat: 40 / 75 * 1.05 = 0.56
    assert lines["backer_screws_box"].net_qty == pytest.approx(0.8)  # 40 sf at 50 sf per box
    assert lines["backer_mesh_tape_roll"].purchase_qty == 1
    est = estimate(scope, catalog, price_book)
    assert est["line_items"][0]["labor_hours"] == pytest.approx(0.5 + 40 * 0.08)
    assert est["line_items"][0]["trade"] == "tile"


def test_floor_underlayment_has_its_own_thinset_on_top_of_the_tile_thinset(catalog):
    lines = takeoff_for(one_room([{"code": "floor_backer_board"}, {"code": "tile_floor"}]), catalog)
    # bedding 40 / 75 plus tile 40 / 50 = 1.333 bags net, 1.4 with waste
    assert lines["thinset_bag"].net_qty == pytest.approx(40 / 75 + 40 / 50, abs=0.001)
    assert lines["thinset_bag"].purchase_qty == 2


def test_tile_floor_without_a_substrate_item_is_flagged(catalog, price_book):
    bare = estimate(one_room([{"code": "tile_floor"}]), catalog, price_book)
    text = " ".join(bare["warnings"])
    assert "'Tile floor' normally follows" in text
    assert "Floor tile underlayment, backer board" in text and "Prep concrete slab for tile" in text
    for substrate in ("floor_backer_board", "slab_floor_prep"):
        est = estimate(one_room([{"code": substrate}, {"code": "tile_floor"}]), catalog, price_book)
        assert not any("Tile floor" in w for w in est["warnings"]), substrate


def test_bathroom_floor_underlayment_goes_after_paint_and_before_floor_tile(example):
    est = estimate(example("bathroom"))
    steps = stage_steps(est)
    board = steps[("floor_backer_board", "main")]
    assert steps[("prime_paint_walls", "main")]["end_day"] <= board["start_day"]
    assert board["end_day"] <= steps[("tile_floor", "set")]["start_day"]
    assert not any("Tile floor" in w for w in est["warnings"])
    assert any("1/4 inch cement backer board" in a for a in est["assumptions"])


def test_every_example_tile_floor_has_a_substrate(example):
    for name in ("bathroom", "kitchen", "basement"):
        est = estimate(example(name))
        assert not any("normally follows" in w for w in est["warnings"]), name


# ---- 2. electrical rough-in and trim-out -------------------------------------

def test_electrical_is_split_into_rough_and_finish_visits(example):
    est = estimate(example("bathroom"))
    steps = stage_steps(est)
    hours = hours_by_stage(est)
    for code in ("device", "exhaust_fan"):
        rough, finish = steps[(code, "rough")], steps[(code, "finish")]
        assert rough["phase_name"] == "Rough plumbing and electrical"
        assert finish["phase_name"] == "Finish electrical"
        # rough-in is closed up by insulation and board; trim-out waits for paint and wall tile
        assert rough["end_day"] <= steps[("insulate_walls", "main")]["start_day"]
        assert rough["end_day"] <= steps[("drywall_walls", "hang")]["start_day"]
        assert finish["start_day"] >= steps[("prime_paint_walls", "main")]["end_day"]
        assert finish["start_day"] >= steps[("tile_shower_walls", "grout")]["end_day"] + 1
        assert hours[(code, "rough")] > hours[(code, "finish")] > 0
    # 3 devices: 0.6 hours each at rough-in, 0.3 each at trim-out plus half an hour of setup
    assert hours[("device", "rough")] == pytest.approx(1.8)
    assert hours[("device", "finish")] == pytest.approx(0.5 + 0.9)


def test_recessed_lights_are_cabled_at_rough_and_installed_at_finish(example):
    est = estimate(example("kitchen"))
    steps = stage_steps(est)
    assert steps[("recessed_light", "rough")]["end_day"] <= steps[("drywall_walls", "hang")]["start_day"]
    assert steps[("recessed_light", "finish")]["start_day"] >= steps[("backsplash_tile", "grout")]["end_day"] + 1


def test_staged_item_materials_are_bought_once(catalog):
    lines = takeoff_for(one_room([{"code": "device", "qty": 3}, {"code": "exhaust_fan"},
                                  {"code": "recessed_light", "qty": 4}]), catalog)
    assert lines["device_kit"].purchase_qty == 3
    assert lines["bath_fan_80cfm"].purchase_qty == 1
    assert lines["led_wafer_light"].purchase_qty == 4


def test_shower_valve_trim_goes_on_after_the_tile(example):
    steps = stage_steps(estimate(example("bathroom")))
    assert steps[("shower_valve", "rough")]["end_day"] <= steps[("cement_board", "main")]["start_day"]
    assert steps[("shower_valve", "trim")]["start_day"] >= steps[("tile_shower_walls", "grout")]["end_day"] + 1


# ---- 3. drywall coats --------------------------------------------------------

COATS = ["hang", "tape", "second", "finish", "sand"]


@pytest.mark.parametrize("code", ["drywall_walls", "drywall_ceiling"])
def test_drywall_is_hung_then_three_coats_with_dry_days_then_sanded(example, code):
    steps = stage_steps(estimate(example("bathroom")))
    assert [stage for (c, stage) in steps if c == code] == COATS
    for earlier, later in zip(COATS, COATS[1:]):
        gap = steps[(code, later)]["start_day"] - steps[(code, earlier)]["end_day"]
        if earlier in ("tape", "second", "finish"):
            assert steps[(code, earlier)]["wait_days"] == 1
            assert steps[(code, earlier)]["wait_for"] == "dry"
            assert gap >= 1, (earlier, later)
        else:
            assert gap >= 0
    assert steps[(code, "sand")]["end_day"] <= steps[("paint_ceiling", "main")]["start_day"]
    # more than the old single half day visit: four working days from first board to sanded
    assert steps[(code, "sand")]["end_day"] - steps[(code, "hang")]["start_day"] >= 4


def test_ceiling_drywall_carries_tape_compound_and_screws(catalog):
    lines = takeoff_for(one_room([{"code": "drywall_ceiling", "qty": 450}]), catalog)
    assert lines["drywall_sheet_4x8"].purchase_qty == 16  # 450 / 32 * 1.12 = 15.75
    assert lines["joint_compound_bucket"].net_qty == pytest.approx(1.0)  # one bucket per 450 sf, all coats
    assert lines["drywall_tape_roll"].net_qty == pytest.approx(450 / 675, abs=0.001)
    assert lines["drywall_screws_box"].net_qty == pytest.approx(0.45)


def test_wall_drywall_compound_is_not_double_counted_across_coats(catalog):
    lines = takeoff_for(one_room([{"code": "drywall_walls", "qty": 450}]), catalog)
    assert lines["joint_compound_bucket"].net_qty == pytest.approx(1.0)
    assert lines["drywall_screws_box"].net_qty == pytest.approx(0.45)


@pytest.mark.parametrize("code", ["drywall_walls", "drywall_ceiling"])
def test_drywall_materials_match_the_published_quantities_per_thousand_sf(catalog, code):
    """Source checked: 370 ft of tape and 1,250 screws per 1,000 sf, compound inside 9 to 11 gallons."""
    lines = takeoff_for(one_room([{"code": code, "qty": 1000}]), catalog)
    assert lines["drywall_tape_roll"].net_qty * 250 == pytest.approx(370, abs=1)  # 250 ft rolls
    assert lines["drywall_screws_box"].net_qty == pytest.approx(1.0)  # one 5 lb box, about 1,250 screws
    gallons = lines["joint_compound_bucket"].net_qty * 4.5
    assert 9 <= gallons <= 11
    assert gallons == pytest.approx(10, abs=0.01)


def test_drywall_dry_time_assumption_states_the_conditions(example):
    notes = " ".join(estimate(example("bathroom"))["assumptions"])
    assert "17 hours at 70 F and 50 percent humidity" in notes
    assert "cool damp basement" in notes and "50 F or warmer" in notes
    assert "Level 4" in notes


def test_ceilings_come_before_walls_for_drywall_and_paint(example):
    steps = stage_steps(estimate(example("bathroom")))
    assert steps[("drywall_ceiling", "hang")]["end_day"] <= steps[("drywall_walls", "hang")]["start_day"]
    assert steps[("paint_ceiling", "main")]["end_day"] <= steps[("prime_paint_walls", "main")]["start_day"]


def test_shower_is_waterproofed_and_tiled_while_the_drywall_coats_dry(example):
    steps = stage_steps(estimate(example("bathroom")))
    waterproof = steps[("shower_waterproof", "main")]
    assert waterproof["start_day"] >= steps[("cement_board", "main")]["end_day"]
    assert waterproof["start_day"] < steps[("drywall_walls", "sand")]["end_day"]
    # paint still waits for both tracks: sanded drywall and cured wall tile
    paint = steps[("paint_ceiling", "main")]
    assert paint["start_day"] >= steps[("drywall_walls", "sand")]["end_day"]
    assert paint["start_day"] >= steps[("tile_shower_walls", "grout")]["end_day"] + 1


# ---- 4. baseboard deductions -------------------------------------------------

def test_baseboard_length_deducts_doors_and_listed_runs():
    plain = Room("Bath", 8, 5, 8, doors=1, windows=1)
    assert plain.baseboard_lf == 26 - 3
    bath = Room("Bath", 8, 5, 8, doors=1, windows=1,
                base_deductions=(("shower alcove", 10.0), ("vanity", 3.0)))
    assert bath.baseboard_lf == 10
    assert Room("Closet", 2, 2, 7, doors=2, base_deductions=(("shelf", 9.0),)).baseboard_lf == 0


def test_baseboard_takeoff_uses_the_deducted_length(catalog, price_book):
    deductions = [{"label": "shower alcove", "lf": 10}, {"label": "vanity", "lf": 3}]
    est = estimate(one_room([{"code": "baseboard"}], base_deductions=deductions), catalog, price_book)
    item = est["line_items"][0]
    assert item["quantity"] == 10
    assert item["labor_hours"] == pytest.approx(0.5 + 10 * 0.05)
    assert est["rooms"][0]["baseboard_lf"] == 10
    assert est["rooms"][0]["perimeter_lf"] == 26
    sticks = next(line for line in est["takeoff"] if line["sku"] == "baseboard_8ft")
    assert sticks["purchase_qty"] == 2  # 10 / 8 * 1.10 = 1.375, not the 4 a full perimeter would buy
    note = next(a for a in est["assumptions"] if "baseboard is" in a)
    assert "10 lf" in note and "26 lf perimeter" in note and "vanity" in note and "door" in note


def test_scope_without_base_deductions_still_parses_and_deducts_doors_only(catalog, price_book):
    est = estimate(one_room([{"code": "baseboard"}]), catalog, price_book)
    assert est["rooms"][0]["base_deductions"] == []
    assert est["line_items"][0]["quantity"] == 23


def test_explicit_baseboard_qty_still_wins(catalog, price_book):
    est = estimate(one_room([{"code": "baseboard", "qty": 18}],
                            base_deductions=[{"label": "vanity", "lf": 3}]), catalog, price_book)
    assert est["line_items"][0]["quantity"] == 18
    assert not any("baseboard is" in a for a in est["assumptions"])


@pytest.mark.parametrize("bad", [
    "3 ft", [{"label": "vanity"}], [{"lf": 3}], [{"label": "", "lf": 3}],
    [{"label": "vanity", "lf": -1}], [{"label": "vanity", "lf": "3"}], [3],
])
def test_bad_base_deductions_are_rejected(catalog, bad):
    with pytest.raises(ScopeError, match="base_deductions"):
        parse_scope(one_room([{"code": "baseboard"}], base_deductions=bad), catalog)


def test_examples_deduct_fixture_and_cabinet_runs(example):
    rooms = {r["name"]: r for name in ("bathroom", "kitchen", "basement")
             for r in estimate(example(name))["rooms"]}
    assert rooms["Hall bath"]["baseboard_lf"] == 10  # 26 less door 3, shower alcove 10, vanity 3
    assert rooms["Kitchen"]["baseboard_lf"] == 24.5  # 52 less 2 doors, cabinets and appliances
    assert rooms["Half bath"]["baseboard_lf"] == 16
    assert all(r["baseboard_lf"] < r["perimeter_lf"] for r in rooms.values())


# ---- 5. backer board screw box size ------------------------------------------

def test_backer_screw_box_size_is_stated_in_catalog_price_book_and_output(catalog, price_book, example):
    for code in ("cement_board", "floor_backer_board"):
        assert any("box of 185 screws" in note for note in catalog.items[code].notes), code
    assert "box of 185" in price_book.material("backer_screws_box").description
    est = estimate(example("bathroom"))
    assert any("box of 185 screws" in a for a in est["assumptions"])
    assert "box of 185 screws" in " ".join(render_text(est).split())


def test_backer_screw_coverage_matches_the_stated_box(catalog):
    # 185 screws: about 2 per sf on walls (90 sf a box), about 3.7 per sf on floors (50 sf a box)
    walls = takeoff_for(one_room([{"code": "cement_board", "qty": 90}]), catalog)
    floor = takeoff_for(one_room([{"code": "floor_backer_board", "qty": 50}]), catalog)
    assert walls["backer_screws_box"].net_qty == pytest.approx(1.0)
    assert floor["backer_screws_box"].net_qty == pytest.approx(1.0)
    assert 185 / 90 == pytest.approx(2.0, abs=0.1)
    assert 185 / 50 == pytest.approx(3.7)


# ---- 6. production rates -----------------------------------------------------

def test_sample_production_rates(catalog):
    """Pins the sample rates so a change is deliberate. Hours per unit, all visits added."""
    expected = {
        "demo_fixtures": 1.25, "rough_plumbing": 4.0, "shower_valve": 2.75, "device": 0.9,
        "recessed_light": 0.8, "exhaust_fan": 2.5, "drywall_walls": 0.045, "drywall_ceiling": 0.06,
        "cement_board": 0.05, "floor_backer_board": 0.08, "tile_floor": 0.16, "tile_shower_walls": 0.22,
        "prime_paint_walls": 0.025, "baseboard": 0.05, "paint_trim": 0.04,
    }
    for code, rate in expected.items():
        assert catalog.items[code].hours_per_unit == pytest.approx(rate), code


def test_bathroom_gut_labor_hours(example):
    est = estimate(example("bathroom"))
    hours = {entry["trade"]: entry["hours"] for entry in est["labor"]}
    total = sum(hours.values())
    assert total == pytest.approx(sum(i["labor_hours"] for i in est["line_items"]), abs=0.05)
    assert total == pytest.approx(104.04, abs=0.05)
    assert hours["plumbing"] == pytest.approx(21.5)  # 3 fixtures roughed in, valve, pan, 3 fixtures set
    assert hours["tile"] > hours["plumbing"] > hours["demolition"] > hours["drywall"]


# ---- sequencing rules --------------------------------------------------------

def test_one_trade_in_one_room_and_phase_is_a_single_visit(example):
    est = estimate(example("bathroom"))
    demo = [s for s in est["schedule"]["steps"] if s["trade"] == "demolition"]
    assert len(demo) == 4
    assert demo[0]["start_day"] == 0
    assert demo[-1]["end_day"] == 1.0  # 13.81 hours for a crew of two is one day, not four half days
    for earlier, later in zip(demo, demo[1:]):
        assert earlier["end_day"] == later["start_day"]


@pytest.mark.parametrize("name", ["bathroom", "kitchen", "basement"])
def test_no_trade_overlaps_itself_in_any_example(example, name):
    by_trade = {}
    for step in estimate(example(name))["schedule"]["steps"]:
        assert step["end_day"] >= step["start_day"]
        by_trade.setdefault(step["trade"], []).append((step["start_day"], step["end_day"]))
    for trade, spans in by_trade.items():
        spans.sort()
        for (_, end), (start, _) in zip(spans, spans[1:]):
            assert end <= start, trade


def test_floating_floor_goes_in_after_the_base_cabinets(example):
    est = estimate(example("kitchen"))
    steps = stage_steps(est)
    assert steps[("base_cabinets", "main")]["end_day"] <= steps[("lvp_floor", "main")]["start_day"]
    floor = next(i for i in est["line_items"] if i["code"] == "lvp_floor")
    assert floor["quantity"] == 168 - 14 * 2  # cabinet footprint taken out


def test_trim_is_painted_after_it_is_installed(example):
    steps = stage_steps(estimate(example("bathroom")))
    assert steps[("baseboard", "main")]["end_day"] <= steps[("paint_trim", "main")]["start_day"]
    assert steps[("interior_door", "main")]["end_day"] <= steps[("paint_door", "main")]["start_day"]
    assert steps[("paint_door", "main")]["end_day"] <= steps[("final_clean", "main")]["start_day"]


def test_basement_walls_on_the_slab_get_a_treated_bottom_plate(example):
    est = estimate(example("basement"))
    lines = {line["sku"]: line for line in est["takeoff"]}
    assert lines["pt_2x4_8"]["purchase_qty"] == 15  # 102 lf / 8 * 1.10 = 14.03
    assert lines["concrete_fastener_box"]["purchase_qty"] == 1
    # studs at 16 inches plus two top plate runs: 102 / 1.333 + 102 / 4, no third untreated plate
    assert lines["stud_2x4_8"]["net_qty"] == pytest.approx(102 / 1.333 + 102 / 4, abs=0.01)


# ---- catalog rules for stages and phases -------------------------------------

def test_catalog_rejects_bad_stages_and_phase_order():
    good = json.loads(DEFAULT_CATALOG.read_text(encoding="utf-8"))

    def broken(change):
        data = copy.deepcopy(good)
        change(data)
        return data

    def item(data, code):
        return next(i for i in data["items"] if i["code"] == code)

    with pytest.raises(CatalogError, match="strictly later phases"):
        catalog_from_dict(broken(lambda d: item(d, "device")["stages"].reverse()))
    with pytest.raises(CatalogError, match="stage keys must be unique"):
        catalog_from_dict(broken(lambda d: item(d, "device")["stages"][1].update(key="rough")))
    with pytest.raises(CatalogError, match="at least one stage"):
        catalog_from_dict(broken(lambda d: item(d, "device").update(stages=[])))
    with pytest.raises(CatalogError, match="'after' must name earlier phases"):
        catalog_from_dict(broken(lambda d: d["phases"]["60"].update(after=[65])))


def test_phase_blockers(catalog):
    assert catalog.blockers(10) == frozenset()
    assert 58 not in catalog.blockers(60) and 50 in catalog.blockers(60) and 10 in catalog.blockers(60)
    assert catalog.blockers(65) == catalog.blockers(60) | {60}
    assert {58, 65} <= catalog.blockers(70)


# ---- output ------------------------------------------------------------------

def test_report_prints_assumptions_and_wait_reasons(example):
    text = render_text(estimate(example("bathroom")))
    assert "ASSUMPTIONS" in text
    assert "then wait 1 day to dry" in text and "then wait 1 day to cure" in text
    assert "baseboard 10 lf" in text
    notes = text[text.index("ASSUMPTIONS"):].splitlines()[1:]
    assert len(notes) > 5 and all(len(line) <= 96 for line in notes)


EM_DASH = chr(0x2014)


def test_no_em_dashes_in_the_repository():
    checked = 0
    for folder in ("", ".github/workflows", "data", "demo", "demo/site", "docs", "examples", "remodel_estimator", "tests",
                   "web"):
        for path in (REPO_ROOT / folder).glob("*"):
            if path.suffix in (".py", ".json", ".md", ".txt", ".js", ".html", ".css", ".toml", ".yml"):
                assert EM_DASH not in path.read_text(encoding="utf-8"), path
                checked += 1
    assert checked > 20


def test_web_ui_shows_the_assumptions_list():
    html = (REPO_ROOT / "web" / "index.html").read_text(encoding="utf-8")
    script = (REPO_ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert 'id="assumptions"' in html
    assert "est.assumptions" in script and "base_deductions" in script


def test_readme_excerpt_is_real_output(example):
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    block = readme.split("## What you will see")[1].split("```")[1]
    real = {line.rstrip() for line in render_text(estimate(example("bathroom"))).splitlines()}
    excerpt = [line for line in block.strip("\n").splitlines() if line.strip() and line != "..."]
    assert len(excerpt) > 50
    for line in excerpt:
        assert line in real, line


# ---- source checked defaults (see docs/assumptions.md) ------------------------

TILE_CASES = [("bathroom", "tile_floor", 0.13, 0.03), ("bathroom", "tile_shower_walls", 0.18, 0.04),
              ("kitchen", "backsplash_tile", 0.16, 0.04), ("basement", "tile_floor", 0.13, 0.03)]


@pytest.mark.parametrize("name,code,set_rate,grout_rate", TILE_CASES)
def test_tile_is_set_then_grouted_after_the_mortar_cures(example, catalog, name, code, set_rate, grout_rate):
    """Grout makers ask for 24 to 48 hours of mortar cure before grouting, so grout is its own visit."""
    item = catalog.items[code]
    assert [stage.key for stage in item.stages] == ["set", "grout"]
    assert item.stages[0].hours_per_unit == pytest.approx(set_rate)
    assert item.stages[1].hours_per_unit == pytest.approx(grout_rate)
    assert item.stages[1].setup_hours == 0
    assert {use.sku for use in item.stages[1].materials} == {"grout_bag"}
    assert "grout_bag" not in {use.sku for use in item.stages[0].materials}
    steps = stage_steps(estimate(example(name)))
    tile, grout = steps[(code, "set")], steps[(code, "grout")]
    assert tile["wait_days"] == 1 and tile["wait_for"] == "cure"
    assert grout["wait_days"] == 1 and grout["wait_for"] == "cure"
    assert grout["start_day"] >= tile["end_day"] + 1
    assert grout["phase_name"].endswith("grout")


def test_splitting_tile_into_two_visits_does_not_change_hours_or_materials(catalog, price_book):
    est = estimate(one_room([{"code": "slab_floor_prep"}, {"code": "tile_floor"}]), catalog, price_book)
    hours = hours_by_stage(est)
    assert hours[("tile_floor", "set")] + hours[("tile_floor", "grout")] == pytest.approx(1.0 + 40 * 0.16)
    lines = {line["sku"]: line for line in est["takeoff"]}
    assert lines["grout_bag"]["purchase_qty"] == 1 and lines["grout_bag"]["net_qty"] == pytest.approx(0.4)
    assert lines["tile_floor_sf"]["purchase_qty"] == 44


def test_wall_tile_grout_stays_on_the_shower_track(catalog):
    # the grout visit follows the wall tile, not the drywall sanding, and paint waits for it
    assert 58 not in catalog.blockers(67) and 65 in catalog.blockers(67)
    assert {58, 65, 67} <= catalog.blockers(70)
    assert catalog.phase_name(82) == "Floor tile grout" and catalog.phase_name(102) == "Backsplash grout"


def test_finish_plumbing_waits_for_the_floor_grout(example):
    steps = stage_steps(estimate(example("bathroom")))
    grout = steps[("tile_floor", "grout")]
    for code in ("vanity", "set_toilet"):
        assert steps[(code, "main")]["start_day"] >= grout["end_day"] + 1, code


def test_floating_plank_waste_is_the_makers_ten_percent(catalog):
    lines = takeoff_for(one_room([{"code": "lvp_floor", "qty": 236}]), catalog)
    assert lines["lvp_carton"].waste_pct == 10.0
    assert lines["lvp_carton"].net_qty == pytest.approx(10.0)
    assert lines["lvp_carton"].purchase_qty == 11
    assert any("10 percent" in note for note in catalog.items["lvp_floor"].notes)


def test_membrane_coverage_sits_inside_the_data_sheet_range(catalog, example):
    lines = takeoff_for(one_room([{"code": "cement_board", "qty": 100}, {"code": "shower_waterproof", "qty": 100}]),
                        catalog)
    finished_sf_per_gallon = 100 / lines["waterproof_membrane_gal"].net_qty
    assert 40 <= finished_sf_per_gallon <= 55  # two coats; data sheets give 40 to 55
    assert finished_sf_per_gallon == pytest.approx(50)
    notes = " ".join(estimate(example("bathroom"))["assumptions"])
    assert "40 to 55 sf per gallon" in notes and "flood test" in notes


def test_paint_coverage_sits_inside_the_data_sheet_range(catalog):
    uses = {use.sku: use for use in catalog.items["prime_paint_walls"].materials}
    assert uses["primer_gal"].multiplier == 1 and 300 <= uses["primer_gal"].coverage <= 400
    assert uses["wall_paint_gal"].multiplier == 2 and 300 <= uses["wall_paint_gal"].coverage <= 400


def test_slab_wall_fasteners_are_inside_the_evaluated_spacing(catalog):
    uses = {use.sku: use for use in catalog.items["frame_walls_slab"].materials}
    spacing_inches = uses["concrete_fastener_box"].coverage / 100 * 12  # box of 100
    assert spacing_inches <= 36  # the tighter of the two evaluation reports allows 3 ft
    assert any("3 or 4 ft" in note for note in catalog.items["frame_walls_slab"].notes)


def test_production_rates_are_labelled_as_judgment_not_as_checked():
    raw = json.loads(DEFAULT_CATALOG.read_text(encoding="utf-8"))
    assert "judgment" in raw["_rates"]
    assert "sanity checked" not in raw["_rates"]
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "sanity checked" not in readme


def test_readme_assumptions_table_and_notes_file_agree():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Assumptions a contractor should check")[1].split("\n## ")[0]
    rows = [line for line in section.splitlines() if line.startswith("| ") and not line.startswith("| ---")][1:]
    assert len(rows) >= 25
    statuses = {row.split("|")[3].strip() for row in rows}
    assert statuses == {"confirmed", "corrected", "judgment"}
    assert "samples a contractor should replace" in section
    notes = (REPO_ROOT / "docs" / "assumptions.md").read_text(encoding="utf-8")
    for row in rows:
        topic = row.split("|")[1].strip()
        assert topic in notes, topic
    assert "Could not open" in notes
