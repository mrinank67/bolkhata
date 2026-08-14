"""Token verification — the single gate in front of every user-scoped route."""

from unittest import mock

import pytest
from fastapi import HTTPException

from auth import verify_token


def test_missing_header_is_401():
    with pytest.raises(HTTPException) as exc:
        verify_token(None)
    assert exc.value.status_code == 401


def test_empty_header_is_401():
    with pytest.raises(HTTPException) as exc:
        verify_token("")
    assert exc.value.status_code == 401


@pytest.mark.parametrize(
    "header",
    [
        "test-token",  # no scheme
        "Basic test-token",  # wrong scheme
        "bearer test-token",  # lowercase: startswith("Bearer ") is case-sensitive
        "Bearer",  # scheme with no token
    ],
)
def test_malformed_header_is_401(header):
    with pytest.raises(HTTPException) as exc:
        verify_token(header)
    assert exc.value.status_code == 401


def test_firebase_rejection_becomes_401_without_leaking_details():
    with mock.patch("auth.auth.verify_id_token", side_effect=ValueError("expired: iat in future")):
        with pytest.raises(HTTPException) as exc:
            verify_token("Bearer expired-token")

    assert exc.value.status_code == 401
    # The upstream reason must not reach the client.
    assert "iat" not in str(exc.value.detail)
    assert exc.value.detail == "Invalid Authentication Token"


def test_valid_token_returns_uid():
    with mock.patch("auth.auth.verify_id_token", return_value={"uid": "abc123"}) as verify:
        assert verify_token("Bearer good-token") == "abc123"
    verify.assert_called_once_with("good-token")


def test_token_containing_the_scheme_string_is_split_correctly():
    """`split("Bearer ")[1]` is fragile if the token itself contains the scheme."""
    with mock.patch("auth.auth.verify_id_token", return_value={"uid": "u"}) as verify:
        verify_token("Bearer abcBearer xyz")
    # Documents actual behaviour: the naive split truncates at the second
    # occurrence. Real Firebase JWTs are base64url and cannot contain a space,
    # so this is not exploitable — but the test pins the behaviour so a future
    # change to the parsing is a deliberate one.
    assert verify.call_args[0][0] == "abc"


def test_route_without_token_returns_401_over_http(client):
    """End-to-end: the dependency actually runs on a mounted route."""
    assert client.get("/history").status_code == 401
