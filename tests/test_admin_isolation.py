"""The support console must stay invisible to, and separate from, the main app.

Every assertion here guards something that would break silently:

  * a link added to index.html would advertise the console to every shopkeeper;
  * a missing sw.js rule would cache another shop's ledger onto the operator's
    device, where it would outlive the support session;
  * a missing vercel.json rule would serve /admin as the shopkeeper's app, or
    404 the admin API in production while every local test still passed.
"""

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

INDEX = REPO_ROOT / "index.html"
ADMIN = REPO_ROOT / "admin.html"
SW = REPO_ROOT / "sw.js"
VERCEL = REPO_ROOT / "vercel.json"


@pytest.fixture(scope="module")
def vercel() -> dict:
    return json.loads(VERCEL.read_text(encoding="utf-8"))


class TestTheMainAppNeverMentionsIt:
    def test_index_html_has_no_link_to_the_console(self):
        assert "admin" not in INDEX.read_text(encoding="utf-8").lower()

    def test_no_shopkeeper_facing_module_imports_the_admin_code(self):
        """js/admin/* may import js/*, never the other way round."""
        offenders = []
        for path in (REPO_ROOT / "js").glob("*.js"):
            if "admin" in path.read_text(encoding="utf-8"):
                offenders.append(path.name)
        assert not offenders, f"main-app modules referencing admin: {offenders}"

    def test_app_js_does_not_load_the_console(self):
        assert "admin" not in (REPO_ROOT / "app.js").read_text(encoding="utf-8").lower()


class TestServiceWorker:
    def test_the_console_is_not_precached(self):
        """admin.html in STATIC_ASSETS would put the console on every device."""
        source = SW.read_text(encoding="utf-8")
        assets = source.split("STATIC_ASSETS")[1].split("]")[0]
        assert "admin" not in assets

    def test_admin_requests_bypass_the_cache(self):
        """The worker's scope is "/", so it controls /admin whether or not
        admin.html registers it. Without this rule the network-first branch
        would write another shop's data into Cache Storage."""
        source = SW.read_text(encoding="utf-8")
        fetch_handler = source.split('addEventListener("fetch"')[1]
        assert '"/admin/"' in fetch_handler or "'/admin/'" in fetch_handler

    def test_the_cache_version_was_bumped_past_v19(self):
        """Existing installs keep the old rules until the version changes."""
        version = re.search(r'CACHE_VERSION = "bolkhata-v(\d+)"', SW.read_text(encoding="utf-8"))
        assert version, "CACHE_VERSION is not in the expected form"
        assert int(version.group(1)) >= 20


class TestVercelRouting:
    def _routes(self, vercel):
        return vercel["routes"]

    def _first_match(self, path, routes):
        for rule in routes:
            if re.fullmatch(rule["src"], path):
                return rule
        return None

    def test_the_console_page_is_served_as_itself(self, vercel):
        """Not as index.html, which the catch-all would otherwise do."""
        assert self._first_match("/admin", self._routes(vercel))["dest"] == "/admin.html"

    def test_the_admin_api_reaches_the_backend(self, vercel):
        for path in ("/admin/me", "/admin/users", "/admin/users/abc/voice-logs", "/admin/audit"):
            rule = self._first_match(path, self._routes(vercel))
            assert rule and rule["dest"] == "/main.py", path

    def test_the_page_rule_does_not_swallow_the_api(self, vercel):
        """`/admin` is an anchored exact match; `/admin/x` must not hit it."""
        assert self._first_match("/admin/me", self._routes(vercel))["dest"] == "/main.py"

    def test_the_new_ledger_entry_paths_are_routable(self, vercel):
        rule = self._first_match("/ledger/entry/abc123", self._routes(vercel))
        assert rule and rule["dest"] == "/main.py"

    def test_the_new_purchase_paths_are_routable(self, vercel):
        rule = self._first_match("/suppliers/purchase/abc123", self._routes(vercel))
        assert rule and rule["dest"] == "/main.py"

    def test_the_console_assets_are_built(self, vercel):
        built = {b["src"] for b in vercel["builds"]}
        assert "admin.html" in built
        assert "admin.css" in built
        # js/** already covers js/admin/**.
        assert "js/**" in built

    def test_the_console_page_is_never_cached(self, vercel):
        for header in vercel["headers"]:
            if header["source"] == "/admin.html":
                values = {h["key"]: h["value"] for h in header["headers"]}
                assert "no-store" in values["Cache-Control"]
                return
        pytest.fail("no Cache-Control header rule for /admin.html")


class TestTheConsolePage:
    def test_it_does_not_register_the_service_worker(self):
        """A worker registered from this page would cache support data."""
        assert "serviceWorker" not in ADMIN.read_text(encoding="utf-8")

    def test_it_is_marked_noindex(self):
        assert "noindex" in ADMIN.read_text(encoding="utf-8")
