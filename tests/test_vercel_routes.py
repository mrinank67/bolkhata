"""vercel.json route-table parity.

vercel.json hand-enumerates every API path (its `routes` array) and ends with a
catch-all that serves index.html. Adding an endpoint to routes/ without adding a
matching rule therefore yields a 404 in production that is completely invisible
locally, because uvicorn serves every mounted route regardless.

Note the gap this specifically guards: there is no `/ledger/(.*)` catch-all —
the four /ledger paths are listed one by one — so a new /ledger endpoint silently
falls through to the SPA fallback.
"""

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
VERCEL_JSON = REPO_ROOT / "vercel.json"

# Paths the SPA fallback is supposed to own.
STATIC_PREFIXES = ("/js/", "/icons/")
STATIC_FILES = {"/app.js", "/sw.js", "/manifest.json", "/styles.css", "/index.html"}


@pytest.fixture(scope="module")
def vercel_config() -> dict:
    return json.loads(VERCEL_JSON.read_text(encoding="utf-8"))


def _sample_path(fastapi_path: str) -> str:
    """Turn '/orders/{order_id}/bill' into a concrete '/orders/sample/bill'."""
    return re.sub(r"\{[^}]+\}", "sample", fastapi_path)


def _first_match(path: str, routes: list[dict]) -> dict | None:
    """Vercel evaluates `routes` in order; the first matching rule wins."""
    for rule in routes:
        src = rule.get("src")
        if src and re.match(f"^{src}$", path):
            return rule
    return None


def _api_paths(app) -> list[str]:
    paths = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if not path:
            continue
        # Starlette's built-in docs/openapi routes are not deployed behind
        # vercel.json rules and are irrelevant here.
        if path in ("/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"):
            continue
        paths.add(path)
    return sorted(paths)


def test_every_api_route_is_reachable_through_vercel_json(app, vercel_config):
    routes = vercel_config["routes"]
    unreachable = []

    for fastapi_path in _api_paths(app):
        concrete = _sample_path(fastapi_path)
        rule = _first_match(concrete, routes)
        if rule is None or rule.get("dest") != "/main.py":
            unreachable.append((fastapi_path, concrete, rule.get("dest") if rule else None))

    assert not unreachable, (
        "These FastAPI routes do not reach /main.py through vercel.json and will "
        "404 (or serve index.html) in production. Add a `routes` entry:\n"
        + "\n".join(
            f"  {p}  (as {c})  ->  {dest or 'NO MATCHING RULE'}" for p, c, dest in unreachable
        )
    )


def test_static_assets_are_not_swallowed_by_api_rules(vercel_config):
    """A too-greedy API rule added above the static rules would break the PWA."""
    routes = vercel_config["routes"]
    for path in sorted(STATIC_FILES):
        if path == "/index.html":
            continue
        rule = _first_match(path, routes)
        assert rule is not None, f"{path} matches no rule"
        assert rule.get("dest") == path, (
            f"{path} is served by {rule.get('dest')} instead of itself — "
            "an API rule above the static rules is shadowing it"
        )

    for prefix in STATIC_PREFIXES:
        probe = f"{prefix}probe.js"
        rule = _first_match(probe, routes)
        assert rule is not None and rule.get("dest", "").startswith(prefix), (
            f"{probe} is served by {rule.get('dest') if rule else None}, "
            f"expected a {prefix} passthrough"
        )


def test_spa_fallback_is_last(vercel_config):
    """The catch-all must stay at the bottom or it shadows everything below it."""
    routes = vercel_config["routes"]
    catch_all_indexes = [i for i, r in enumerate(routes) if r.get("src") == "/(.*)"]
    assert catch_all_indexes, "vercel.json lost its SPA fallback rule"
    assert catch_all_indexes[-1] == len(routes) - 1, (
        "the /(.*) fallback is not the last rule — every rule after it is dead"
    )
    assert routes[-1].get("dest") == "/index.html"


def test_every_deployed_source_file_has_a_build_entry(vercel_config):
    """`builds` is an explicit allowlist; a file missing from it is not deployed."""
    built = {b["src"] for b in vercel_config["builds"]}
    for required in ("main.py", "index.html", "app.js", "js/**", "styles.css", "sw.js"):
        assert required in built, f"{required} is missing from vercel.json builds"


def test_vercel_json_and_manifest_are_valid_json():
    """A malformed config breaks the deploy with no local signal at all."""
    for name in ("vercel.json", "manifest.json", "firebase.json"):
        path = REPO_ROOT / name
        assert path.exists(), f"{name} is missing"
        json.loads(path.read_text(encoding="utf-8"))
