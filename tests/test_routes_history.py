"""GET/DELETE /history."""

import datetime

UID = "test-uid"
HISTORY = f"users/{UID}/history"


def _entry(days_ago=0, results=None, errors=None):
    return {
        "results": results if results is not None else ["Added 2kg rice"],
        "errors": errors if errors is not None else [],
        "timestamp": datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days_ago),
    }


def test_empty_history(authed_client, fake_db):
    assert authed_client.get("/history").json() == {"history": []}


def test_returns_entries_newest_first(authed_client, fake_db):
    fake_db.seed(f"{HISTORY}/old", _entry(days_ago=5, results=["oldest"]))
    fake_db.seed(f"{HISTORY}/mid", _entry(days_ago=2, results=["middle"]))
    fake_db.seed(f"{HISTORY}/new", _entry(days_ago=0, results=["newest"]))

    entries = authed_client.get("/history").json()["history"]

    assert [e["results"][0] for e in entries] == ["newest", "middle", "oldest"]


def test_timestamp_is_serialized_as_iso8601(authed_client, fake_db):
    fake_db.seed(f"{HISTORY}/a", _entry())
    ts = authed_client.get("/history").json()["history"][0]["timestamp"]
    datetime.datetime.fromisoformat(ts)  # must parse


def test_missing_timestamp_becomes_null(authed_client, fake_db):
    fake_db.seed(f"{HISTORY}/a", {"results": [], "errors": []})
    assert authed_client.get("/history").json()["history"][0]["timestamp"] is None


def test_response_is_capped_at_50_entries(authed_client, fake_db):
    """Unbounded history would grow the payload without limit on a busy shop."""
    for i in range(60):
        fake_db.seed(f"{HISTORY}/e{i:03d}", _entry(days_ago=i))

    assert len(authed_client.get("/history").json()["history"]) == 50


def test_errors_are_returned_alongside_results(authed_client, fake_db):
    fake_db.seed(f"{HISTORY}/a", _entry(results=[], errors=["Item not found: chawal"]))
    entry = authed_client.get("/history").json()["history"][0]
    assert entry["errors"] == ["Item not found: chawal"]


def test_delete_clears_only_the_callers_history(authed_client, fake_db):
    fake_db.seed(f"{HISTORY}/a", _entry())
    fake_db.seed(f"{HISTORY}/b", _entry())
    fake_db.seed("users/someone-else/history/x", _entry())

    assert authed_client.delete("/history").json() == {"status": "cleared"}

    assert fake_db.paths_under(HISTORY) == []
    assert "users/someone-else/history/x" in fake_db.docs


def test_delete_on_empty_history_is_a_no_op(authed_client, fake_db):
    assert authed_client.delete("/history").status_code == 200


def test_history_is_scoped_to_the_authenticated_user(authed_client, fake_db):
    fake_db.seed("users/someone-else/history/x", _entry(results=["not yours"]))
    assert authed_client.get("/history").json()["history"] == []
