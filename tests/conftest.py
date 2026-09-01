"""Shared fixtures.

Import order in this file is load-bearing and intentionally not alphabetical:
``main.py`` calls ``init_firebase()`` at module scope, so the patch has to be
installed *before* ``import main`` or the test run tries to reach real Firebase.
"""

import os
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# ---------------------------------------------------------------------------
# 1. Deterministic environment.
#
# main.py calls load_dotenv(), which does NOT override variables already set.
# Setting them here first means a developer's real .env cannot leak into
# assertions, so tests behave identically locally and in CI.
# ---------------------------------------------------------------------------
TEST_ENV = {
    "FIREBASE_API_KEY": "test-api-key",
    "FIREBASE_AUTH_DOMAIN": "test.firebaseapp.com",
    "FIREBASE_PROJECT_ID": "test-project",
    "FIREBASE_STORAGE_BUCKET": "test-bucket.appspot.com",
    "FIREBASE_MESSAGING_SENDER_ID": "000000000000",
    "FIREBASE_APP_ID": "1:000000000000:web:testappid",
    "FIREBASE_MEASUREMENT_ID": "G-TEST00000",
    "GROQ_API_KEY": "test-groq-key",
    "SARVAM_API_KEY": "test-sarvam-key",
    "PAY_LINK_SECRET": "test-pay-link-secret",
    "BILL_TOKEN_SECRET": "test-bill-token-secret",
    "ALLOWED_ORIGINS": "",
    "DEBUG_LOGS": "",
}
os.environ.update(TEST_ENV)

# ---------------------------------------------------------------------------
# 2. Patch Firebase init, then import the app.
# ---------------------------------------------------------------------------
from tests.fakes import FakeFirestore  # noqa: E402

_FAKE_DB = FakeFirestore()

_patcher = mock.patch("auth.init_firebase", return_value=_FAKE_DB)
_patcher.start()

import main  # noqa: E402

_patcher.stop()

assert main.db is _FAKE_DB, "main.db was not replaced by the fake — patch order is wrong"

TEST_UID = "test-uid"
ADMIN_UID = "admin-uid"
# The shop a support session is acting on — deliberately neither TEST_UID nor
# ADMIN_UID, so a test that asserts a write landed here cannot pass by accident
# because the seam silently fell back to the caller's own uid.
TARGET_UID = "target-uid"

# Paths Starlette/FastAPI mount for themselves. No test in this suite is about
# the docs UI, and none of them are deployed behind vercel.json rules.
DOCS_PATHS = frozenset({"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"})


def iter_routes(app):
    """Yield every leaf route, flattening whatever ``app.routes`` nests them in.

    Starlette changed this shape underneath us: it used to hand back one flat
    list of ``Route``/``APIRoute`` objects, and now returns an opaque
    ``_IncludedRouter`` per ``include_router()`` call, holding the real routes
    on ``original_router``. Tests that walked ``app.routes`` directly did not
    fail loudly on that change -- they skipped every entry lacking ``.path``
    and quietly asserted nothing -- so this walks the tree instead. It handles
    both shapes, and ``Mount``/sub-application nesting, so a future change of
    the same kind cannot silently empty these tests again.
    """
    stack = list(app.routes)
    while stack:
        route = stack.pop()
        included = getattr(route, "original_router", None)
        if included is not None:
            stack.extend(included.routes)
            continue
        nested = getattr(route, "routes", None)
        if nested:
            stack.extend(nested)
            continue
        yield route


def route_methods(app, *, skip_docs: bool = True) -> set[tuple[str, str]]:
    """``{(method, path)}`` for every real endpoint, e.g. ``("GET", "/orders")``.

    HEAD and OPTIONS are dropped: Starlette adds them implicitly, so they are
    not decisions anyone made in this codebase.
    """
    found = set()
    for route in iter_routes(app):
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods:
            continue
        if skip_docs and path in DOCS_PATHS:
            continue
        found.update((m, path) for m in methods if m not in ("HEAD", "OPTIONS"))
    return found


@pytest.fixture
def fake_db() -> FakeFirestore:
    """The single Firestore double bound to ``main.db``.

    Routes resolve it lazily (``from main import db`` inside each handler), so
    mutating this instance is enough — never rebind ``main.db``.
    """
    _FAKE_DB.reset()
    return _FAKE_DB


@pytest.fixture
def app():
    return main.app


@pytest.fixture
def client(app):
    """Unauthenticated client. Requests without a valid token should 401."""
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c


@pytest.fixture
def authed_client(client):
    """Client whose Authorization header verifies to TEST_UID.

    Patches ``auth.auth.verify_id_token`` — the ``firebase_admin.auth`` symbol
    imported into auth.py. Patching ``auth.verify_token`` would not work: every
    router did ``from auth import verify_token`` at import time and holds its
    own reference.
    """
    with mock.patch("auth.auth.verify_id_token", return_value={"uid": TEST_UID}):
        client.headers.update({"Authorization": "Bearer test-token"})
        yield client


@pytest.fixture
def admin_client(client):
    """Client whose token carries the admin custom claim.

    Same patch point as authed_client — the decoded token gains ``admin: True``,
    which is exactly what a real token granted by scripts/grant_admin.py looks
    like to the server.
    """
    with mock.patch("auth.auth.verify_id_token", return_value={"uid": ADMIN_UID, "admin": True}):
        client.headers.update({"Authorization": "Bearer admin-token"})
        yield client


@pytest.fixture
def user_path() -> str:
    return f"users/{TEST_UID}"


@pytest.fixture
def target_path() -> str:
    return f"users/{TARGET_UID}"


@pytest.fixture(autouse=True)
def _direct_transactions():
    """Run ``@firestore.transactional`` callables directly against the fake.

    The real decorator drives a live backend session and rejects a
    ``FakeTransaction``. The fake applies writes immediately, so invoking the
    wrapped callable as-is reproduces what a committed transaction would leave
    behind. Autouse because order-number allocation is transactional, and that
    now runs on every order-creating path in the app.
    """
    with mock.patch("routes.orders.firestore.transactional", lambda f: f):
        yield


@pytest.fixture(autouse=True)
def _no_network(monkeypatch, request):
    """Fail loudly if a test reaches the real network.

    Opt out with ``@pytest.mark.allow_network`` (nothing currently needs it).
    """
    if request.node.get_closest_marker("allow_network"):
        return

    def _blocked(*args, **kwargs):
        raise AssertionError("A test attempted a real network call. Stub requests/httpx instead.")

    import requests

    monkeypatch.setattr(requests, "post", _blocked)
    monkeypatch.setattr(requests, "get", _blocked)
