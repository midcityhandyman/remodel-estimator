"""Command line interface.

    python -m remodel_estimator estimate examples/bathroom.json
    python -m remodel_estimator estimate examples/kitchen.json --json
    python -m remodel_estimator examples
    python -m remodel_estimator catalog
    python -m remodel_estimator serve
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .catalog import CatalogError, load_catalog
from .engine import estimate
from .models import ScopeError
from .paths import EXAMPLES_DIR
from .pricebook import PriceBookError, load_price_book
from .report import render_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="remodel_estimator", description="Remodel estimating engine.")
    sub = parser.add_subparsers(dest="command", required=True)

    est = sub.add_parser("estimate", help="estimate a scope file")
    est.add_argument("scope", help="path to a scope JSON file, or an example name such as 'bathroom'")
    est.add_argument("--price-book", help="path to a price book JSON file (default: bundled sample)")
    est.add_argument("--json", action="store_true", help="print the full estimate as JSON")

    sub.add_parser("examples", help="list the bundled example scopes")
    sub.add_parser("catalog", help="list the work items you can put in a scope")

    serve = sub.add_parser("serve", help="run the API and web UI")
    serve.add_argument("--host", default=os.environ.get("REMODEL_HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("REMODEL_PORT", "8000")))
    serve.add_argument("--price-book", help="path to a price book JSON file (default: bundled sample)")
    return parser


def resolve_scope_path(value: str) -> Path:
    path = Path(value)
    if path.exists():
        return path
    example = EXAMPLES_DIR / f"{value}.json"
    return example if example.exists() else path


def cmd_estimate(args: argparse.Namespace) -> int:
    path = resolve_scope_path(args.scope)
    try:
        scope_data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"error: cannot read scope file: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"error: {path} is not valid JSON: {exc}", file=sys.stderr)
        return 2
    try:
        result = estimate(scope_data, load_catalog(), load_price_book(args.price_book))
    except ScopeError as exc:
        print("error: the scope has problems:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2) if args.json else render_text(result))
    return 0


def cmd_examples() -> int:
    for path in sorted(EXAMPLES_DIR.glob("*.json")):
        project = json.loads(path.read_text(encoding="utf-8")).get("project", "")
        print(f"{path.stem:<12} {project}")
    return 0


def cmd_catalog() -> int:
    catalog = load_catalog()
    print(f"{'code':<20} {'trade':<11} {'basis':<13} name")
    current = None
    for item in sorted(catalog.items.values(), key=lambda i: (i.phase, catalog.order[i.code])):
        if item.phase != current:
            current = item.phase
            print(f"-- {catalog.phase_name(item.phase)}")
        print(f"{item.code:<20} {item.trade:<11} {item.basis:<13} {item.name}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .api import make_server

    server = make_server(args.host, args.port, price_book=load_price_book(args.price_book))
    host, port = server.server_address[:2]
    print(f"Remodel estimator running at http://{host}:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "estimate":
            return cmd_estimate(args)
        if args.command == "examples":
            return cmd_examples()
        if args.command == "catalog":
            return cmd_catalog()
        return cmd_serve(args)
    except (PriceBookError, CatalogError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
