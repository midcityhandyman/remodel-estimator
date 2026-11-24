"""Build the read-only static demo site: the bundled examples, estimated by the real engine.

    python demo/build_static_site.py            writes ./dist
    python demo/build_static_site.py some/dir   writes some/dir

The output needs no server. It holds one JSON file per example (exactly what the
engine returns for that scope) and a static page that renders them. Standard
library only. All prices are sample values; nothing here is a quote.

tests/test_static_site.py checks that the JSON written here equals what the
engine and the API return for the same scopes, so the demo cannot drift.
"""

from __future__ import annotations

import html
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from remodel_estimator import estimate, load_catalog, load_price_book  # noqa: E402

EXAMPLES = ("bathroom", "kitchen", "basement")
SITE_SRC = ROOT / "demo" / "site"
SHARED_STYLE = ROOT / "web" / "style.css"
DEFAULT_OUT = ROOT / "dist"
SUMMARY_MARKER = "<!-- EXAMPLE_CARDS -->"
NOTICE_MARKER = "<!-- PRICE_BOOK_NOTICE -->"


def load_scope(name: str) -> dict:
    return json.loads((ROOT / "examples" / f"{name}.json").read_text(encoding="utf-8"))


def build_estimates() -> dict[str, dict]:
    """Run the engine on every bundled example with the bundled sample price book."""
    catalog, price_book = load_catalog(), load_price_book()
    return {name: estimate(load_scope(name), catalog, price_book) for name in EXAMPLES}


def money(amount: float) -> str:
    return f"${amount:,.2f}"


def example_cards(estimates: dict[str, dict]) -> str:
    """Static summary cards, so the totals are in the page even before any script runs."""
    cards = []
    for name, est in estimates.items():
        cards.append(
            f'<a class="card example" href="#{html.escape(name)}" data-name="{html.escape(name)}">'
            f'<div class="label">{html.escape(est["project"])}</div>'
            f'<div class="value">{money(est["totals"]["total"])}</div>'
            f'<div class="label">sample total, {est["schedule"]["working_days"]} working days</div>'
            "</a>"
        )
    return "\n      ".join(cards)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def build(out_dir: Path = DEFAULT_OUT) -> dict[str, dict]:
    """Write the static site into out_dir (replacing it) and return the estimates used."""
    estimates = build_estimates()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "data").mkdir(parents=True)

    for name, est in estimates.items():
        write_text(out_dir / "data" / f"{name}.json", json.dumps(est, indent=2) + "\n")
    index = {
        "examples": [
            {"name": name, "project": est["project"], "file": f"data/{name}.json"}
            for name, est in estimates.items()
        ],
    }
    write_text(out_dir / "data" / "index.json", json.dumps(index, indent=2) + "\n")

    notice = next(iter(estimates.values()))["price_book"]["notice"]
    page = (SITE_SRC / "index.html").read_text(encoding="utf-8")
    for marker in (SUMMARY_MARKER, NOTICE_MARKER):
        if marker not in page:
            raise SystemExit(f"demo/site/index.html is missing the {marker} marker")
    page = page.replace(SUMMARY_MARKER, example_cards(estimates)).replace(NOTICE_MARKER, html.escape(notice))
    write_text(out_dir / "index.html", page)

    for asset in ("demo.js", "demo.css"):
        write_text(out_dir / asset, (SITE_SRC / asset).read_text(encoding="utf-8"))
    write_text(out_dir / "style.css", SHARED_STYLE.read_text(encoding="utf-8"))
    return estimates


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    out_dir = Path(args[0]).resolve() if args else DEFAULT_OUT
    estimates = build(out_dir)
    print(f"Static demo site written to {out_dir}")
    for name, est in estimates.items():
        print(f"  {name:<10} {money(est['totals']['total']):>12}  (sample prices)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
