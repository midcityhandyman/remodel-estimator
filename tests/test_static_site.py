"""The static demo site must be exactly what the engine returns, and must say it is a sample."""

import importlib.util
import json
import threading
import urllib.request

import pytest

from remodel_estimator import estimate, load_catalog, load_price_book
from remodel_estimator.api import make_server
from remodel_estimator.cli import main as cli_main
from remodel_estimator.paths import EXAMPLES_DIR, REPO_ROOT

SCRIPT = REPO_ROOT / "demo" / "build_static_site.py"


def load_builder():
    spec = importlib.util.spec_from_file_location("build_static_site", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def builder():
    return load_builder()


@pytest.fixture(scope="module")
def site(builder, tmp_path_factory):
    out = tmp_path_factory.mktemp("site") / "dist"
    builder.build(out)
    return out


def scope_for(name):
    return json.loads((EXAMPLES_DIR / f"{name}.json").read_text(encoding="utf-8"))


def test_site_covers_every_bundled_example(builder, site):
    bundled = {path.stem for path in EXAMPLES_DIR.glob("*.json")}
    assert set(builder.EXAMPLES) == bundled
    index = json.loads((site / "data" / "index.json").read_text(encoding="utf-8"))
    assert [e["name"] for e in index["examples"]] == list(builder.EXAMPLES)
    for entry in index["examples"]:
        assert (site / entry["file"]).is_file()


@pytest.mark.parametrize("name", ["bathroom", "kitchen", "basement"])
def test_static_json_equals_engine_output(site, name):
    static = json.loads((site / "data" / f"{name}.json").read_text(encoding="utf-8"))
    fresh = estimate(scope_for(name), load_catalog(), load_price_book())
    assert static == json.loads(json.dumps(fresh))
    assert static["price_book"]["sample"] is True


def test_static_json_equals_api_response(site):
    server = make_server("127.0.0.1", 0, quiet=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/api/estimate"
        for name in ("bathroom", "kitchen", "basement"):
            request = urllib.request.Request(
                url, data=json.dumps(scope_for(name)).encode("utf-8"), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=10) as response:
                reply = json.loads(response.read())
            assert json.loads((site / "data" / f"{name}.json").read_text(encoding="utf-8")) == reply
    finally:
        server.shutdown()
        server.server_close()


def test_page_shows_the_totals_the_cli_prints(site, capsys):
    page = (site / "index.html").read_text(encoding="utf-8")
    for name in ("bathroom", "kitchen", "basement"):
        assert cli_main(["estimate", name]) == 0
        total_line = next(line for line in capsys.readouterr().out.splitlines() if "ESTIMATE TOTAL" in line)
        assert total_line.split()[-1] in page


def test_page_is_labelled_sample_and_read_only(site):
    page = (site / "index.html").read_text(encoding="utf-8")
    assert "Sample prices only. This is not a quote." in page
    assert "read-only demo of the three example scopes bundled with the project" in page
    assert "clone" in page and "run it locally" in page
    assert load_price_book().notice in page
    assert "<!--" not in page
    assert "<form" not in page and "<input" not in page and "<button" not in page


def test_site_is_self_contained(site):
    files = {p.relative_to(site).as_posix() for p in site.rglob("*") if p.is_file()}
    assert files == {
        "index.html", "demo.js", "demo.css", "style.css",
        "data/index.json", "data/bathroom.json", "data/kitchen.json", "data/basement.json",
    }
    script = (site / "demo.js").read_text(encoding="utf-8")
    assert "/api/" not in script and "innerHTML" not in script.replace("never innerHTML", "")
    assert (site / "style.css").read_text(encoding="utf-8") == (REPO_ROOT / "web" / "style.css").read_text(encoding="utf-8")
    for name in files:
        assert chr(0x2014) not in (site / name).read_text(encoding="utf-8")


def test_build_replaces_stale_output(builder, tmp_path):
    out = tmp_path / "dist"
    (out / "data").mkdir(parents=True)
    (out / "data" / "stale.json").write_text("{}", encoding="utf-8")
    builder.build(out)
    assert not (out / "data" / "stale.json").exists()
