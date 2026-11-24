import json
import threading
import urllib.error
import urllib.request

import pytest

from remodel_estimator.api import make_server
from remodel_estimator.cli import main
from remodel_estimator.paths import DEFAULT_PRICE_BOOK

from .conftest import one_room


# ---- CLI -------------------------------------------------------------------

def test_cli_text_estimate(capsys):
    assert main(["estimate", "bathroom"]) == 0
    out = capsys.readouterr().out
    assert "ESTIMATE: Sample hall bathroom remodel" in out
    assert "SAMPLE PRICES, NOT REAL" in out
    assert "MATERIAL TAKEOFF BY TRADE" in out
    assert "ESTIMATE TOTAL" in out


def test_cli_json_estimate_from_a_file_path(capsys, tmp_path):
    scope = tmp_path / "scope.json"
    scope.write_text(json.dumps(one_room([{"code": "tile_floor"}])), encoding="utf-8")
    assert main(["estimate", str(scope), "--json"]) == 0
    est = json.loads(capsys.readouterr().out)
    assert est["project"] == "Test project"
    assert est["totals"]["total"] > 0


def test_cli_reports_scope_problems(capsys, tmp_path):
    scope = tmp_path / "bad.json"
    scope.write_text(json.dumps(one_room([{"code": "nope"}])), encoding="utf-8")
    assert main(["estimate", str(scope)]) == 2
    assert "unknown work item code 'nope'" in capsys.readouterr().err


def test_cli_missing_file_and_bad_json(capsys, tmp_path):
    assert main(["estimate", str(tmp_path / "missing.json")]) == 2
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert main(["estimate", str(broken)]) == 2
    assert "not valid JSON" in capsys.readouterr().err


def test_cli_price_book_from_flag_and_environment(capsys, tmp_path, monkeypatch):
    data = json.loads(DEFAULT_PRICE_BOOK.read_text(encoding="utf-8"))
    data["name"] = "Doubled test book"
    for material in data["materials"].values():
        material["unit_price"] *= 2
    book = tmp_path / "book.json"
    book.write_text(json.dumps(data), encoding="utf-8")

    main(["estimate", "bathroom", "--json"])
    base = json.loads(capsys.readouterr().out)
    main(["estimate", "bathroom", "--json", "--price-book", str(book)])
    flagged = json.loads(capsys.readouterr().out)
    monkeypatch.setenv("REMODEL_PRICE_BOOK", str(book))
    main(["estimate", "bathroom", "--json"])
    from_env = json.loads(capsys.readouterr().out)

    assert flagged["price_book"]["name"] == "Doubled test book"
    assert flagged["totals"]["materials"] == pytest.approx(base["totals"]["materials"] * 2)
    assert from_env["totals"] == flagged["totals"]


def test_cli_lists_examples_and_catalog(capsys):
    assert main(["examples"]) == 0
    assert "kitchen" in capsys.readouterr().out
    assert main(["catalog"]) == 0
    out = capsys.readouterr().out
    assert "tile_floor" in out and "-- Demolition" in out


# ---- API -------------------------------------------------------------------

@pytest.fixture(scope="module")
def base_url():
    server = make_server("127.0.0.1", 0, quiet=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def call(url, payload=None, raw=None):
    data = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None)
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.headers.get("Content-Type"), response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("Content-Type"), error.read()


def test_api_health_marks_prices_as_sample(base_url):
    status, _, body = call(f"{base_url}/api/health")
    data = json.loads(body)
    assert status == 200
    assert data["status"] == "ok"
    assert data["price_book"]["sample"] is True


def test_api_catalog_and_examples(base_url):
    _, _, body = call(f"{base_url}/api/catalog")
    catalog = json.loads(body)
    assert any(item["code"] == "tile_floor" for item in catalog["items"])
    assert catalog["phases"][0]["name"] == "Demolition"

    _, _, body = call(f"{base_url}/api/examples")
    assert [e["name"] for e in json.loads(body)["examples"]] == ["basement", "bathroom", "kitchen"]

    status, _, body = call(f"{base_url}/api/examples/kitchen")
    assert status == 200 and json.loads(body)["project"] == "Sample kitchen remodel"
    assert call(f"{base_url}/api/examples/nope")[0] == 404
    assert call(f"{base_url}/api/unknown")[0] == 404


def test_api_estimate_round_trip(base_url, example):
    from remodel_estimator import estimate

    status, content_type, body = call(f"{base_url}/api/estimate", example("bathroom"))
    assert status == 200
    assert content_type.startswith("application/json")
    assert json.loads(body) == estimate(example("bathroom"))


def test_api_estimate_rejects_bad_input(base_url):
    status, _, body = call(f"{base_url}/api/estimate", one_room([{"code": "nope"}]))
    assert status == 400
    assert any("nope" in p for p in json.loads(body)["problems"])

    status, _, body = call(f"{base_url}/api/estimate", raw=b"{not json")
    assert status == 400
    assert "not valid JSON" in json.loads(body)["error"]

    assert call(f"{base_url}/api/other", {"x": 1})[0] == 404


def test_api_serves_the_web_ui(base_url):
    status, content_type, body = call(f"{base_url}/")
    assert status == 200 and content_type.startswith("text/html")
    assert b"Remodel Estimator" in body
    status, content_type, _ = call(f"{base_url}/app.js")
    assert status == 200 and content_type.startswith("text/javascript")
    assert call(f"{base_url}/style.css")[0] == 200


@pytest.mark.parametrize("path", ["/../data/price_book.sample.json", "/..%2Fdata%2Fcatalog.json",
                                  "/../README.md", "/missing.html"])
def test_api_does_not_serve_files_outside_web(base_url, path):
    assert call(f"{base_url}{path}")[0] == 404
