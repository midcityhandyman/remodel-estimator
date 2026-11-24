import json

import pytest

from remodel_estimator.catalog import load_catalog
from remodel_estimator.paths import DEFAULT_PRICE_BOOK, EXAMPLES_DIR
from remodel_estimator.pricebook import price_book_from_dict


@pytest.fixture(scope="session")
def catalog():
    return load_catalog()


@pytest.fixture(scope="session")
def price_book_data():
    return json.loads(DEFAULT_PRICE_BOOK.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def price_book(price_book_data):
    return price_book_from_dict(price_book_data)


@pytest.fixture
def example():
    def load(name):
        return json.loads((EXAMPLES_DIR / f"{name}.json").read_text(encoding="utf-8"))

    return load


def one_room(items, **room):
    """Build a one room scope: an 8 x 5 x 8 bath with one door and one window by default."""
    base = {"name": "Test room", "length_ft": 8, "width_ft": 5, "height_ft": 8, "doors": 1, "windows": 1}
    base.update(room)
    base["work_items"] = items
    return {"project": "Test project", "rooms": [base]}
