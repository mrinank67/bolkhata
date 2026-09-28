"""POST /process_voice — the only path that spends money on external APIs.

Every test here stubs Sarvam (requests.post) and Groq. The autouse network guard
in conftest.py turns any unstubbed call into a failure rather than a real
request, so an accidental live call cannot slip through.

The ordering asserted below is the point of the endpoint's design: auth, then
per-user cooldown, then the global quotas, and only then the paid API calls. A
regression that moves a rate-limit check after the Sarvam call would burn quota
on requests that were meant to be rejected.
"""

import datetime
import json
from unittest import mock

import pytest
import requests

import prompts
import rate_limiter
from tests.audio_helpers import empty_container, encode, speech

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


class TestLongClipsAreSplit:
    """Sarvam's REST STT refuses audio over 30 s with a 400; long lists used to fail.

    These use real encoded audio (tests/audio_helpers.py) so the decode and the
    split actually run.
    """

    @pytest.fixture
    def per_chunk(self, sarvam):
        """Answer each chunk with its own filename, so order is checkable."""

        def _post(url, headers, data, files):
            resp = mock.MagicMock(status_code=200)
            resp.json.return_value = {"transcript": f"<{files['file'][0]}>"}
            resp.raise_for_status.return_value = None
            return resp

        sarvam.post.side_effect = _post
        return sarvam

    def _send(self, client, audio: bytes):
        return client.post("/process_voice", files={"audio": ("clip.webm", audio, "audio/webm")})

    def test_a_short_clip_is_sent_exactly_as_uploaded(self, authed_client, fake_db, sarvam, groq):
        audio = encode(speech(("talk", 5)))
        self._send(authed_client, audio)

        sarvam.post.assert_called_once()
        name, body, mime = sarvam.post.call_args.kwargs["files"]["file"]
        assert (name, body, mime) == ("clip.webm", audio, "audio/webm")

    def test_a_long_clip_is_transcribed_in_chunks_and_rejoined_in_order(
        self, authed_client, fake_db, per_chunk, groq
    ):
        audio = encode(
            speech(("talk", 25), ("pause", 0.6), ("talk", 25), ("pause", 0.6), ("talk", 10))
        )
        resp = self._send(authed_client, audio)

        assert resp.status_code == 200
        assert resp.json()["raw_text"] == "<chunk0.wav> <chunk1.wav> <chunk2.wav>"
        for call in per_chunk.post.call_args_list:
            assert call.kwargs["files"]["file"][2] == "audio/wav"
        user_msg = groq.client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        assert "<chunk0.wav> <chunk1.wav> <chunk2.wav>" in user_msg

    def test_every_chunk_is_charged_to_the_sarvam_quota(
        self, authed_client, fake_db, per_chunk, groq
    ):
        audio = encode(speech(("talk", 25), ("pause", 0.6), ("talk", 20)))
        self._send(authed_client, audio)

        assert per_chunk.post.call_count == 2
        assert len(fake_db.docs["_system/rate_limits"]["sarvam_rpm"]) == 2

    def test_no_quota_for_the_extra_chunks_means_no_sarvam_call(
        self, authed_client, fake_db, sarvam, groq, monkeypatch
    ):
        def _limit(db, config, cost=None):
            # Only the extra-chunk charge passes a cost; the up-front checks pass.
            return (False, 12.0) if cost is not None else (True, 0.0)

        monkeypatch.setattr("routes.voice.check_global_rate_limit", _limit)
        resp = self._send(authed_client, encode(speech(("talk", 25), ("pause", 0.6), ("talk", 20))))

        assert resp.status_code == 429
        sarvam.post.assert_not_called()

    def test_the_log_records_duration_and_chunk_count(
        self, authed_client, fake_db, per_chunk, groq
    ):
        self._send(authed_client, encode(speech(("talk", 25), ("pause", 0.6), ("talk", 20))))

        (entry,) = [v for k, v in fake_db.docs.items() if "/voice_logs/" in k]
        assert entry["stt_chunks"] == 2
        assert entry["audio_seconds"] == pytest.approx(45.6, abs=0.2)

    def test_a_tap_with_no_audio_never_reaches_sarvam(self, authed_client, fake_db, sarvam, groq):
        body = self._send(authed_client, empty_container()).json()

        assert "too short" in body["message"].lower()
        sarvam.post.assert_not_called()

    def test_over_two_minutes_is_refused_before_any_sarvam_call(
        self, authed_client, fake_db, sarvam, groq, monkeypatch
    ):
        # A 2-minute encode is slow for a unit test; lower the cap instead.
        monkeypatch.setattr("routes.voice.MAX_AUDIO_SECONDS", 30)
        body = self._send(authed_client, encode(speech(("talk", 35)))).json()

        assert "too long" in body["message"].lower()
        sarvam.post.assert_not_called()

    def test_one_chunk_hitting_429_twice_fails_the_request_as_rate_limited(
        self, authed_client, fake_db, sarvam, groq, monkeypatch
    ):
        monkeypatch.setattr("routes.voice.time.sleep", lambda _s: None)
        sarvam.status_code = 429

        resp = self._send(authed_client, encode(speech(("talk", 25), ("pause", 0.6), ("talk", 20))))

        assert resp.status_code == 429


class TestSarvamRequest:
    def test_sarvam_transcribes_without_translating(self, authed_client, fake_db, sarvam, groq):
        """Translation is Groq's pass: Sarvam's made a per-unit price a line total."""
        _post(authed_client)
        assert sarvam.post.call_args.kwargs["data"]["mode"] == "codemix"

    def test_sarvams_reason_for_a_400_reaches_the_log(self, authed_client, fake_db, sarvam, groq):
        err = requests.HTTPError("400 Client Error: Bad Request")
        err.response = mock.MagicMock(text='{"error": "Audio duration exceeds 30 seconds"}')
        sarvam.raise_for_status.side_effect = err

        resp = _post(authed_client)

        assert resp.status_code == 500
        assert "30 seconds" not in resp.text
        (entry,) = [v for k, v in fake_db.docs.items() if "/voice_logs/" in k]
        assert "Audio duration exceeds 30 seconds" in entry["error_detail"]


class TestContextualTranslation:
    """A Groq pass between Sarvam and intent extraction, for non-English speech.

    Sarvam's own translation rendered "12 rupees each" and "12 rupees for the
    lot" alike; this pass keeps the two apart before the intent model sees them.
    """

    HINDI = "रमेश को 5 Maggi 12 रुपये वाली दे दो"
    ENGLISH = "Give Ramesh 5 Maggi at 12 rupees each."

    @pytest.fixture
    def llm(self, groq):
        """Answer the translation prompt and the intent prompt differently."""
        groq.translation = json.dumps({"english": self.ENGLISH})

        def _create(**kwargs):
            completion = mock.MagicMock()
            system = kwargs["messages"][0]["content"]
            is_translation = system == prompts.get_translation_prompt()
            completion.choices[0].message.content = (
                groq.translation if is_translation else json.dumps(groq.intent)
            )
            return completion

        groq.client.chat.completions.create.side_effect = _create
        return groq

    def _calls(self, llm):
        return [c.kwargs for c in llm.client.chat.completions.create.call_args_list]

    def _log(self, fake_db):
        (entry,) = [v for k, v in fake_db.docs.items() if "/voice_logs/" in k]
        return entry

    def test_the_intent_model_reads_the_translation(self, authed_client, fake_db, sarvam, llm):
        sarvam.transcript = self.HINDI
        body = _post(authed_client).json()

        translate, intent = self._calls(llm)
        assert translate["messages"][0]["content"] == prompts.get_translation_prompt()
        assert translate["messages"][1]["content"] == self.HINDI
        assert translate["model"] == intent["model"], "same Groq model for both passes"
        assert self.ENGLISH in intent["messages"][1]["content"]
        assert self.HINDI not in intent["messages"][1]["content"]
        assert (body["raw_text"], body["translated_text"]) == (self.HINDI, self.ENGLISH)

    def test_the_log_keeps_both_what_was_heard_and_what_was_read(
        self, authed_client, fake_db, sarvam, llm
    ):
        sarvam.transcript = self.HINDI
        _post(authed_client)

        entry = self._log(fake_db)
        assert entry["transcript"] == self.HINDI
        assert entry["translation"] == self.ENGLISH
        assert "translate_ms" in entry

    @pytest.mark.parametrize(
        "heard",
        [
            "ரமேஷுக்கு 2 Maggi 12 ரூபாய் ஒன்றுக்கு",  # Tamil
            "রমেশকে 3টা সাবান, প্রতিটা 30 টাকা",  # Bengali
            "رمیش کو 2 میگی دو",  # Urdu
        ],
    )
    def test_every_indian_script_is_translated(self, authed_client, fake_db, sarvam, llm, heard):
        sarvam.transcript = heard
        _post(authed_client)
        assert len(self._calls(llm)) == 2

    def test_english_speech_skips_the_extra_call(self, authed_client, fake_db, sarvam, llm):
        sarvam.transcript = "give Ramesh 5 maggi at 12 rupees each"
        body = _post(authed_client).json()

        (intent,) = self._calls(llm)
        assert "give Ramesh 5 maggi" in intent["messages"][1]["content"]
        assert body["translated_text"] is None

    def test_the_translation_is_charged_to_the_groq_quotas(
        self, authed_client, fake_db, sarvam, llm
    ):
        sarvam.transcript = self.HINDI
        _post(authed_client)

        limits = fake_db.docs["_system/rate_limits"]
        assert len(limits["groq_rpm"]) == 2
        assert limits["groq_rpd_count"] == 2

    def test_unusable_translation_falls_back_to_the_transcript(
        self, authed_client, fake_db, sarvam, llm
    ):
        sarvam.transcript = self.HINDI
        llm.translation = "Give Ramesh five maggi"  # not JSON

        resp = _post(authed_client)

        assert resp.status_code == 200
        intent = self._calls(llm)[-1]
        assert self.HINDI in intent["messages"][1]["content"]
        entry = self._log(fake_db)
        assert "translation" not in entry
        assert entry["translation_error"]

    def test_no_groq_quota_for_the_translation_skips_it(
        self, authed_client, fake_db, sarvam, llm, monkeypatch
    ):
        """The request already paid for STT and holds a slot for the intent call."""
        seen = []

        def _limit(db, config, cost=1):
            seen.append(config.firestore_key)
            # The up-front checks pass; the translation's own RPM charge is refused.
            refused = config.firestore_key == "groq_rpm" and seen.count("groq_rpm") == 2
            return (False, 20.0) if refused else (True, 0.0)

        monkeypatch.setattr("routes.voice.check_global_rate_limit", _limit)
        sarvam.transcript = self.HINDI

        resp = _post(authed_client)

        assert resp.status_code == 200
        (intent,) = self._calls(llm)
        assert self.HINDI in intent["messages"][1]["content"]
        assert "groq_rpm" in self._log(fake_db)["translation_error"]

    def test_a_groq_429_on_the_translation_is_retried_once(
        self, authed_client, fake_db, sarvam, llm, monkeypatch
    ):
        monkeypatch.setattr("routes.voice.time.sleep", lambda _s: None)
        sarvam.transcript = self.HINDI
        busy = RuntimeError("rate limited")
        busy.status_code = 429
        answer = llm.client.chat.completions.create.side_effect
        responses = iter([busy])

        def _flaky(**kwargs):
            err = next(responses, None)
            if err:
                raise err
            return answer(**kwargs)

        llm.client.chat.completions.create.side_effect = _flaky

        body = _post(authed_client).json()

        assert body["translated_text"] == self.ENGLISH
        assert len(self._calls(llm)) == 3  # 429, retry, intent


class TestPromptsAgree:
    def test_the_translation_writes_the_price_phrases_the_intent_prompt_keys_on(self):
        """If these drift apart, per-unit prices become line totals again."""
        translation, intent = prompts.get_translation_prompt(), prompts.get_system_prompt()
        for phrase in ("each", "per ", "for a total of"):
            assert phrase in translation
            assert phrase in intent


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


class TestRecentCustomerContext:
    """The 2-minute window that puts "aur do de do" on the right customer.

    It is carried to the LLM as a RECENT CONTEXT line in the system prompt. The
    A counter sale's order carries no customer, so it sets no context at all —
    that is what stops two sales a minute apart merging into one order.
    """

    def _seed_order(self, fake_db, customer):
        fake_db.seed(
            f"users/{UID}/orders/o1",
            {
                "customer_name": customer,
                "customer_modifier": "",
                "item": "maggi",
                "quantity": 5,
                "order_id": "o1",
                "order_no": 1,
                "timestamp": datetime.datetime.now(datetime.UTC),
            },
        )

    def _system_prompt(self, groq):
        call = groq.client.chat.completions.create.call_args
        return call.kwargs["messages"][0]["content"]

    def test_a_named_customer_is_carried_forward(self, authed_client, fake_db, sarvam, groq):
        self._seed_order(fake_db, "ramesh")

        _post(authed_client)

        assert "RECENT CONTEXT" in self._system_prompt(groq)
        assert "ramesh" in self._system_prompt(groq)

    def test_a_nameless_counter_sale_is_not(self, authed_client, fake_db, sarvam, groq):
        self._seed_order(fake_db, "")

        _post(authed_client)

        assert "RECENT CONTEXT" not in self._system_prompt(groq)


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
