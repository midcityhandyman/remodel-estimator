"""Locations of the bundled data, examples and web files."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
EXAMPLES_DIR = REPO_ROOT / "examples"
WEB_DIR = REPO_ROOT / "web"

DEFAULT_CATALOG = DATA_DIR / "catalog.json"
DEFAULT_PRICE_BOOK = DATA_DIR / "price_book.sample.json"
