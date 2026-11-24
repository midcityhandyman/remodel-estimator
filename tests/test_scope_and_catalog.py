import copy
import json

import pytest

from remodel_estimator.catalog import CatalogError, catalog_from_dict
from remodel_estimator.models import Room, ScopeError, parse_scope
from remodel_estimator.paths import DEFAULT_CATALOG, EXAMPLES_DIR
from remodel_estimator.pricebook import PriceBookError, price_book_from_dict

from .conftest import one_room


def test_room_geometry():
    room = Room("Bath", 8, 5, 8, doors=1, windows=1)
    assert room.floor_sf == 40
    assert room.ceiling_sf == 40
    assert room.perimeter_lf == 26
    assert room.wall_sf == 26 * 8 - 20 - 12  # gross less one door and one window


def test_wall_area_never_goes_negative():
    assert Room("Closet", 2, 2, 7, doors=4, windows=4).wall_sf == 0


def test_parse_valid_scope(catalog):
    scope = parse_scope(one_room([{"code": "tile_floor"}, {"code": "cement_board", "qty": 30}]), catalog)
    assert scope.project == "Test project"
    assert [s.code for s in scope.rooms[0].selections] == ["tile_floor", "cement_board"]
    assert scope.rooms[0].selections[1].qty == 30


def test_scope_reports_every_problem_at_once(catalog):
    bad = {
        "project": "",
        "rooms": [
            {"name": "A", "length_ft": -3, "width_ft": "wide", "work_items": [{"code": "no_such_item"}]},
            {"name": "B", "length_ft": 10, "width_ft": 10, "work_items": []},
        ],
    }
    with pytest.raises(ScopeError) as excinfo:
        parse_scope(bad, catalog)
    text = " | ".join(excinfo.value.problems)
    assert "project" in text
    assert "rooms[0].length_ft" in text
    assert "rooms[0].width_ft" in text
    assert "no_such_item" in text
    assert "rooms[1].work_items" in text


@pytest.mark.parametrize("data", [None, [], "scope", {"rooms": []}, {"rooms": "x"}])
def test_scope_shape_errors(catalog, data):
    with pytest.raises(ScopeError):
        parse_scope(data, catalog)


def test_manual_basis_items_need_a_quantity(catalog):
    with pytest.raises(ScopeError, match="needs a qty in sf"):
        parse_scope(one_room([{"code": "cement_board"}]), catalog)


@pytest.mark.parametrize("item", [
    {"code": "tile_floor", "qty": 0},
    {"code": "tile_floor", "qty": -5},
    {"code": "tile_floor", "qty": True},
    {"code": "tile_floor", "qty": float("nan")},
    {"code": "tile_floor", "waste": 1.5},
])
def test_bad_quantities_and_waste_are_rejected(catalog, item):
    with pytest.raises(ScopeError):
        parse_scope(one_room([item]), catalog)


def test_duplicate_item_in_a_room_is_rejected(catalog):
    with pytest.raises(ScopeError, match="more than once"):
        parse_scope(one_room([{"code": "tile_floor"}, {"code": "tile_floor"}]), catalog)


def test_catalog_is_covered_by_the_sample_price_book(catalog, price_book):
    trades = {item.trade for item in catalog.items.values()}
    assert price_book.missing(catalog.skus(), trades) == []


def test_catalog_trades_follow_job_order(catalog):
    trades = catalog.trades()
    assert trades[0] == "demolition"
    assert trades[-1] == "general"
    assert trades.index("drywall") < trades.index("paint") < trades.index("trim")


def test_catalog_rejects_bad_definitions():
    good = json.loads(DEFAULT_CATALOG.read_text(encoding="utf-8"))

    def broken(change):
        data = copy.deepcopy(good)
        change(data)
        return data

    with pytest.raises(CatalogError, match="duplicate"):
        catalog_from_dict(broken(lambda d: d["items"].append(d["items"][0])))
    with pytest.raises(CatalogError, match="unknown basis"):
        catalog_from_dict(broken(lambda d: d["items"][0].update(basis="acre")))
    with pytest.raises(CatalogError, match="later phase"):
        catalog_from_dict(broken(lambda d: d["items"][0].update(requires=["final_clean"])))
    with pytest.raises(CatalogError, match="malformed"):
        catalog_from_dict({"phases": {}})


def test_price_book_rejects_bad_values(price_book_data):
    data = copy.deepcopy(price_book_data)
    data["materials"]["toilet"]["unit_price"] = -1
    with pytest.raises(PriceBookError, match="non-negative"):
        price_book_from_dict(data)
    with pytest.raises(PriceBookError, match="malformed"):
        price_book_from_dict({"name": "x"})


def test_sample_price_book_is_labelled_sample(price_book):
    assert price_book.sample is True
    assert "SAMPLE" in price_book.notice
    assert all("(sample)" in m.description for m in price_book.materials.values())


def test_example_scopes_are_valid(catalog):
    names = sorted(p.stem for p in EXAMPLES_DIR.glob("*.json"))
    assert names == ["basement", "bathroom", "kitchen"]
    for name in names:
        data = json.loads((EXAMPLES_DIR / f"{name}.json").read_text(encoding="utf-8"))
        assert parse_scope(data, catalog).rooms
