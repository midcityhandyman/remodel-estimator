"""Offline demo: estimates the three example scopes, then shows an API round trip.

    python demo/run_demo.py

Prints to the terminal and saves the same text to demo/output.txt.
No network access, accounts or keys are needed. All prices are sample values.
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from remodel_estimator import estimate, load_catalog, load_price_book  # noqa: E402
from remodel_estimator.api import make_server  # noqa: E402
from remodel_estimator.report import render_text  # noqa: E402


def api_round_trip(scope: dict) -> list[str]:
    """Start the API on a free local port, post a scope, and summarize the reply."""
    server = make_server("127.0.0.1", 0, quiet=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/api/estimate"
        request = urllib.request.Request(
            url, data=json.dumps(scope).encode("utf-8"), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            status, reply = response.status, json.loads(response.read())
    finally:
        server.shutdown()
        server.server_close()
    return [
        "API ROUND TRIP (local server on a free port, same engine as the CLI)",
        f"  POST /api/estimate -> HTTP {status}",
        f"  project        {reply['project']}",
        f"  price book     {reply['price_book']['name']} (sample: {reply['price_book']['sample']})",
        f"  takeoff lines  {len(reply['takeoff'])}",
        f"  working days   {reply['schedule']['working_days']}",
        f"  total          ${reply['totals']['total']:,.2f} (sample prices)",
    ]


def main() -> int:
    catalog, price_book = load_catalog(), load_price_book()
    lines: list[str] = ["REMODEL ESTIMATOR DEMO", price_book.notice, ""]
    summary = []
    for name in ("bathroom", "kitchen", "basement"):
        scope = json.loads((ROOT / "examples" / f"{name}.json").read_text(encoding="utf-8"))
        result = estimate(scope, catalog, price_book)
        lines.append(render_text(result))
        summary.append((result["project"], result["schedule"]["working_days"], result["totals"]["total"]))

    lines.append("SUMMARY (sample prices)")
    for project, days, total in summary:
        lines.append(f"  {project:<32} {days:>3} working days   ${total:>12,.2f}")
    lines.append("")
    bathroom = json.loads((ROOT / "examples" / "bathroom.json").read_text(encoding="utf-8"))
    lines += api_round_trip(bathroom)

    text = "\n".join(lines) + "\n"
    sys.stdout.write(text)
    (ROOT / "demo" / "output.txt").write_text(text, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
