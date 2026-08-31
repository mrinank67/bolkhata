"""Every user-scoped endpoint rejects an unauthenticated request.

Auth in this app is not a dependency or middleware — each handler calls
verify_token(authorization) in its own body. That is easy to forget on a new
route, and forgetting it exposes another shopkeeper's ledger.

REQUESTS below is a required registry: test_every_route_is_covered fails when a
route exists that is neither listed here nor explicitly exempt, so a new endpoint
cannot be added without someone deciding what its auth story is.

Bodies are valid on purpose. FastAPI validates the request body *before* the
handler runs, so an invalid body would return 422 and never reach the
verify_token call this file is about.
"""

import pytest

from tests.conftest import route_methods

PUBLIC_ROUTES = {
    # The browser fetches its Firebase config before any user can sign in.
    ("GET", "/config"),
    # Bearer-less by design: authorization is the signed token in the query
    # string, so the customer receiving a payment link needs no account.
    ("GET", "/pay"),
}

# (method, path_template, json_body, form_data, files)
REQUESTS = [
    ("GET", "/history", None, None, None),
    ("DELETE", "/history", None, None, None),
    ("POST", "/confirm_clear_inventory", None, None, None),
    ("GET", "/inventory", None, None, None),
    ("POST", "/inventory", None, {"item": "rice", "price": "50"}, None),
    ("PUT", "/inventory/{item_id}", {"quantity": 5}, None, None),
    ("DELETE", "/inventory/{item_id}", None, None, None),
    ("GET", "/suppliers", None, None, None),
    ("GET", "/suppliers/list", None, None, None),
    (
        "POST",
        "/suppliers/purchase",
        {"supplier_name": "s", "item_name": "rice", "quantity": 1, "amount": 100},
        None,
        None,
    ),
    ("POST", "/suppliers/add", {"name": "Sharma Traders"}, None, None),
    ("PUT", "/suppliers/{supplier_id}", {"name": "Sharma Traders"}, None, None),
    ("DELETE", "/suppliers/{supplier_id}", None, None, None),
    ("GET", "/ledger/customers", None, None, None),
    ("POST", "/ledger/entry", {"customer_name": "c", "item": "rice", "quantity": 1}, None, None),
    ("POST", "/ledger/clear", {"customer_name": "c", "amount": 100}, None, None),
    (
        "POST",
        "/ledger/whatsapp-reminder",
        {"customer_name": "c", "whatsapp_number": "9999999999"},
        None,
        None,
    ),
    ("POST", "/pay/create", {"am": 100, "tn": "note"}, None, None),
    ("GET", "/settings", None, None, None),
    ("PUT", "/settings", {"upi_id": "shop@ybl"}, None, None),
    ("GET", "/orders", None, None, None),
    ("POST", "/orders", {"customer_name": "c", "items": []}, None, None),
    (
        "POST",
        "/orders/{order_id}/items",
        {"item": "rice", "quantity": 1, "price": 50},
        None,
        None,
    ),
    ("PUT", "/orders/{order_id}/customer", {"customer_name": "c"}, None, None),
    ("PUT", "/orders/item/{item_id}", {"quantity": 2}, None, None),
    ("DELETE", "/orders/item/{item_id}", None, None, None),
    ("DELETE", "/orders/{order_id}", None, None, None),
    ("POST", "/orders/{order_id}/bill", None, None, None),
    ("POST", "/orders/{order_id}/bill/touch", None, None, None),
    (
        "POST",
        "/process_voice",
        None,
        None,
        {"audio": ("clip.webm", b"\x00\x01\x02", "audio/webm")},
    ),
    ("POST", "/voice/resolve", {"transaction": {}, "selected_modifier": ""}, None, None),
]


def _concrete(path: str) -> str:
    return (
        path.replace("{item_id}", "sample-item")
        .replace("{supplier_id}", "sample-supplier")
        .replace("{order_id}", "sample-order")
    )


def _send(client, method, path, json_body, form, files):
    return client.request(method, _concrete(path), json=json_body, data=form, files=files)


@pytest.mark.parametrize(
    ("method", "path", "json_body", "form", "files"),
    REQUESTS,
    ids=[f"{m} {p}" for m, p, *_ in REQUESTS],
)
def test_route_requires_authentication(client, fake_db, method, path, json_body, form, files):
    resp = _send(client, method, path, json_body, form, files)
    assert resp.status_code == 401, (
        f"{method} {path} returned {resp.status_code} without a token. "
        "Every user-scoped handler must call verify_token(authorization) first."
    )


@pytest.mark.parametrize(
    ("method", "path", "json_body", "form", "files"),
    REQUESTS,
    ids=[f"{m} {p}" for m, p, *_ in REQUESTS],
)
def test_route_rejects_malformed_bearer_token(
    client, fake_db, method, path, json_body, form, files
):
    client.headers.update({"Authorization": "Bearer not-a-real-jwt"})
    resp = _send(client, method, path, json_body, form, files)
    assert resp.status_code == 401


def test_every_route_is_covered(app):
    """No endpoint may exist without a decision about its auth behaviour."""
    registered = {(m, p) for m, p, *_ in REQUESTS} | PUBLIC_ROUTES

    actual = route_methods(app)
    assert actual, "no routes were discovered — the walk in iter_routes() is broken"

    missing = actual - registered
    assert not missing, (
        "New endpoint(s) with no entry in tests/test_routes_auth.py. Add each to "
        "REQUESTS (with a valid body) so its auth behaviour is asserted, or to "
        "PUBLIC_ROUTES if it is deliberately unauthenticated:\n"
        + "\n".join(f"  {m} {p}" for m, p in sorted(missing))
    )

    stale = registered - actual - PUBLIC_ROUTES
    assert not stale, "REQUESTS lists routes that no longer exist: " + ", ".join(
        f"{m} {p}" for m, p in sorted(stale)
    )


def test_public_routes_do_not_require_a_token(client):
    assert client.get("/config").status_code == 200
    # /pay rejects the token's *content*, not its absence — it must not 401.
    assert client.get("/pay", params={"token": "bogus"}).status_code == 200
