"""POST /process_voice — the only path that spends money on external APIs.

Every test here stubs Sarvam (requests.post) and Groq. The autouse network guard
in conftest.py turns any unstubbed call into a failure rather than a real
request, so an accidental live call cannot slip through.

The ordering asserted below is the point of the endpoint's design: auth, then
per-user cooldown, then the global quotas, and only then the paid API calls. A
regression that moves a rate-limit check after the Sarvam call would burn quota
on requests that were meant to be rejected.
"""

import json
from unittest import mock

import pytest

import rate_limiter

UID = "test-uid"
# Must clear the handler's 100-byte "audio too short" floor, or every test below
# short-circuits before reaching the code it means to exercise.
AUDIO = {"audio": ("clip.webm", b"\x00\x01\x02\x03" * 64, "audio/webm")}


@pytest.fixture(autouse=True)
def passthrough_transactions(monkeypatch):
    monkeypatch.setattr(rate_limiter, "_firestore_transactional", lambda func: func)


@pytest.fixture
def sarvam(monkeypatch):
    """Stub Sarvam STT. Set `.transcript` to change what was 'heard'."""
    stub = mock.MagicMock()
    stub.status_code = 200
    stub.transcript = "do kilo chawal Ramesh ko"
    stub.json.side_effect = lambda: {"transcript": stub.transcript}
    stub.raise_for_status.return_value = None

    post = mock.MagicMock(return_value=stub)
    monkeypatch.setattr("routes.voice.requests.post", post)
    stub.post = post
    return stub


@pytest.fixture
def groq(monkeypatch):
    """Stub the Groq client. Set `.intent` to change the extracted transactions."""
    client = mock.MagicMock()
    holder = mock.MagicMock()
    holder.intent = {"transactions": []}

    def _create(**kwargs):
        completion = mock.MagicMock()
        completion.choices[0].message.content = json.dumps(holder.intent)
        return completion

    client.chat.completions.create.side_effect = _create
    holder.client = client
    monkeypatch.setattr("routes.voice._get_groq_client", lambda: client)
    return holder


def _post(client):
    return client.post("/process_voice", files=AUDIO)


class TestRateLimitingHappensBeforeAnyPaidCall:
    def test_user_cooldown_blocks_the_second_request(self, authed_client, fake_db, sarvam, groq):
        _post(authed_client)
        sarvam.post.reset_mock()

        resp = _post(authed_client)

        assert resp.status_code == 429
        assert resp.json()["status"] == "rate_limited"
        assert resp.headers["Retry-After"]
        # A rate-limited request must not reach Sarvam.
        sarvam.post.assert_not_called()

    def test_daily_cap_message_differs_from_the_cooldown_message(
        self, authed_client, fake_db, sarvam, groq, monkeypatch
    ):
        """retry_after > 10s is how the handler tells the two cases apart."""
        monkeypatch.setattr("routes.voice.check_user_cooldown", lambda db, uid: (False, 3600.0))
        body = _post(authed_client).json()
        assert "tomorrow" in body["message"].lower()

    def test_global_sarvam_quota_blocks_before_the_stt_call(
        self, authed_client, fake_db, sarvam, groq, monkeypatch
    ):
        monkeypatch.setattr(
            "routes.voice.check_global_rate_limit",
            lambda db, config: (
                (False, 30.0) if config.firestore_key == "sarvam_rpm" else (True, 0.0)
            ),
        )
        resp = _post(authed_client)

        assert resp.status_code == 429
        sarvam.post.assert_not_called()

    def test_groq_daily_quota_blocks_before_the_stt_call(
        self, authed_client, fake_db, sarvam, groq, monkeypatch
    ):
        monkeypatch.setattr(
            "routes.voice.check_global_rate_limit",
            lambda db, config: (
                (False, 7200.0) if config.firestore_key == "groq_rpd" else (True, 0.0)
            ),
        )
        resp = _post(authed_client)

        assert resp.status_code == 429
        assert "tomorrow" in resp.json()["message"].lower()
        sarvam.post.assert_not_called()

    def test_unauthenticated_request_never_reaches_the_rate_limiter(
        self, client, fake_db, sarvam, groq
    ):
        """An anonymous caller must not even be able to cause a Firestore write."""
        assert _post(client).status_code == 401
        assert fake_db.docs == {}
        sarvam.post.assert_not_called()


class TestSpeechToText:
    def test_empty_transcript_returns_a_friendly_error(self, authed_client, fake_db, sarvam, groq):
        sarvam.transcript = "   "
        body = _post(authed_client).json()

        assert body["status"] == "error"
        assert "hear" in body["message"].lower()
        groq.client.chat.completions.create.assert_not_called()

    def test_undersized_audio_is_rejected_before_the_stt_call(
        self, authed_client, fake_db, sarvam, groq
    ):
        """A stray tap produces a few bytes; it must not spend Sarvam quota."""
        tiny = {"audio": ("clip.webm", b"\x00" * 50, "audio/webm")}
        body = authed_client.post("/process_voice", files=tiny).json()

        assert body["status"] == "error"
        assert "too short" in body["message"].lower()
        sarvam.post.assert_not_called()

    def test_oversized_audio_is_rejected_before_the_stt_call(
        self, authed_client, fake_db, sarvam, groq
    ):
        big = {"audio": ("clip.webm", b"\x00" * (3 * 1024 * 1024), "audio/webm")}
        body = authed_client.post("/process_voice", files=big).json()

        assert body["status"] == "error"
        assert "too long" in body["message"].lower()
        sarvam.post.assert_not_called()

    def test_stt_failure_does_not_leak_internal_details(self, authed_client, fake_db, sarvam, groq):
        sarvam.raise_for_status.side_effect = RuntimeError(
            "api-subscription-key sk-live-abcdef is invalid"
        )
        resp = _post(authed_client)

        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert "sk-live" not in detail
        assert detail == "Speech recognition failed. Please try again."

    def test_sarvam_429_is_retried_once_then_surfaced(
        self, authed_client, fake_db, sarvam, groq, monkeypatch
    ):
        monkeypatch.setattr("routes.voice.time.sleep", lambda _s: None)
        sarvam.status_code = 429

        resp = _post(authed_client)

        assert resp.status_code == 429
        assert sarvam.post.call_count == 2, "expected exactly one retry"
        assert resp.headers["Retry-After"] == "5"

    def test_sarvam_429_recovering_on_retry_succeeds(
        self, authed_client, fake_db, sarvam, groq, monkeypatch
    ):
        monkeypatch.setattr("routes.voice.time.sleep", lambda _s: None)

        first = mock.MagicMock(status_code=429)
        second = mock.MagicMock(status_code=200)
        second.json.return_value = {"transcript": "do kilo chawal"}
        second.raise_for_status.return_value = None
        sarvam.post.side_effect = [first, second]

        resp = _post(authed_client)

        assert resp.status_code == 200
        assert sarvam.post.call_count == 2

    def test_429_from_sarvam_is_recorded_for_monitoring(
        self, authed_client, fake_db, sarvam, groq, monkeypatch
    ):
        monkeypatch.setattr("routes.voice.time.sleep", lambda _s: None)
        sarvam.status_code = 429

        _post(authed_client)

        assert fake_db.docs["_system/rate_limit_events"]["count_429_sarvam_rpm"] >= 1


class TestIntentExtraction:
    def test_transcript_is_sent_to_groq(self, authed_client, fake_db, sarvam, groq):
        sarvam.transcript = "do kilo chawal Ramesh ko"
        _post(authed_client)

        kwargs = groq.client.chat.completions.create.call_args.kwargs
        assert kwargs["temperature"] == 0.0, "intent extraction must be deterministic"
        assert kwargs["response_format"] == {"type": "json_object"}
        assert "do kilo chawal Ramesh ko" in kwargs["messages"][1]["content"]

    def test_empty_intent_returns_a_result_payload(self, authed_client, fake_db, sarvam, groq):
        groq.intent = {"transactions": []}
        resp = _post(authed_client)

        assert resp.status_code == 200
        assert "status" in resp.json()

    def test_malformed_groq_json_fails_cleanly(self, authed_client, fake_db, sarvam, groq):
        """The LLM is asked for JSON but is not guaranteed to comply.

        Current contract is a 500 with a generic message. That is a deliberate
        upstream-failure response, not a crash — what matters is that the raw
        model output never reaches the client.
        """

        def _bad(**kwargs):
            completion = mock.MagicMock()
            completion.choices[0].message.content = "sorry, I can't do that"
            return completion

        groq.client.chat.completions.create.side_effect = _bad

        resp = _post(authed_client)

        assert resp.status_code == 500
        assert resp.json()["detail"] == "Failed to understand the intent."
        assert "sorry, I can't do that" not in resp.text

    def test_groq_outage_does_not_leak_the_api_key(self, authed_client, fake_db, sarvam, groq):
        groq.client.chat.completions.create.side_effect = RuntimeError(
            "401 Unauthorized: key gsk-live-secret123 rejected"
        )
        resp = _post(authed_client)

        assert resp.status_code == 500
        assert "gsk-live-secret123" not in resp.text


class TestDebugLogging:
    def test_transcripts_are_not_logged_by_default(
        self, authed_client, fake_db, sarvam, groq, capsys
    ):
        """Transcripts are PII — they must stay out of logs unless opted in."""
        sarvam.transcript = "Ramesh ko das hazaar udhaar"
        _post(authed_client)

        assert "Ramesh ko das hazaar" not in capsys.readouterr().out

    def test_debug_logs_flag_enables_transcript_logging(
        self, authed_client, fake_db, sarvam, groq, capsys, monkeypatch
    ):
        monkeypatch.setenv("DEBUG_LOGS", "true")
        sarvam.transcript = "Ramesh ko das hazaar udhaar"
        _post(authed_client)

        assert "Ramesh ko das hazaar" in capsys.readouterr().out
