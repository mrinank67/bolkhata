"""Import-time and app-wiring checks.

These are the cheapest possible guard against the failure mode this app is most
exposed to: main.py initializes Firebase and mounts seven routers at import
scope, so a bad import or a malformed route decorator is a production cold-start
crash rather than anything visible in local development.
"""

from tests.conftest import TEST_ENV, iter_routes

CONFIG_KEYS = {
    "apiKey": "FIREBASE_API_KEY",
    "authDomain": "FIREBASE_AUTH_DOMAIN",
    "projectId": "FIREBASE_PROJECT_ID",
    "storageBucket": "FIREBASE_STORAGE_BUCKET",
    "messagingSenderId": "FIREBASE_MESSAGING_SENDER_ID",
    "appId": "FIREBASE_APP_ID",
    "measurementId": "FIREBASE_MEASUREMENT_ID",
}


def test_app_imports_and_mounts_all_routers(app):
    """Every router in main.py contributed at least one path."""
    paths = {path for r in iter_routes(app) if (path := getattr(r, "path", None))}
    for expected in (
        "/config",
        "/process_voice",
        "/inventory",
        "/history",
        "/suppliers",
        "/ledger/customers",
        "/orders",
        "/orders/{order_id}/bill",
    ):
        assert expected in paths, f"{expected} is missing — a router failed to mount"


def test_openapi_schema_generates(app):
    """Catches malformed decorators and unserializable response models.

    app.openapi() walks every route and every Pydantic model; a broken signature
    raises here instead of on the first production request.
    """
    schema = app.openapi()
    assert schema["openapi"].startswith("3.")
    assert schema["paths"], "no paths in the generated OpenAPI schema"


def test_config_endpoint_returns_firebase_keys(client):
    resp = client.get("/config")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == set(CONFIG_KEYS), "GET /config key set changed"
    for json_key, env_key in CONFIG_KEYS.items():
        assert body[json_key] == TEST_ENV[env_key]


def test_config_requires_no_auth(client):
    """The browser fetches /config before it can possibly hold a token."""
    assert client.get("/config").status_code == 200


def test_cors_allows_localhost_dev_origin(client):
    resp = client.options(
        "/config",
        headers={
            "Origin": "http://localhost:5500",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5500"


def test_cors_rejects_unknown_origin(client):
    resp = client.options(
        "/config",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.headers.get("access-control-allow-origin") is None
