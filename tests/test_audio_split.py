"""audio_split.py — getting a long clip under Sarvam's 30-second REST limit.

Two things matter: no chunk is ever over the limit (that is the 400 this exists
to prevent), and the cuts land in the pauses between words (a word cut in half
transcribes as neither half).
"""

import io
import wave

import av
import pytest

from audio_split import (
    MAX_CHUNK_SECONDS,
    SAMPLE_RATE,
    decode_pcm,
    pcm_seconds,
    split_pcm,
    to_wav,
)
from tests.audio_helpers import empty_container, encode, silence, speech, tone


class TestDecode:
    def test_webm_opus_decodes_to_its_duration(self):
        pcm = decode_pcm(encode(tone(5)))
        assert pcm_seconds(pcm) == pytest.approx(5, abs=0.1)

    def test_safari_mp4_aac_decodes_too(self):
        """iOS Safari's MediaRecorder produces MP4/AAC whatever the client labels it."""
        pcm = decode_pcm(encode(tone(3), fmt="mp4"))
        assert pcm_seconds(pcm) == pytest.approx(3, abs=0.1)

    def test_a_header_with_no_audio_is_empty_not_an_error(self):
        assert decode_pcm(empty_container()) == b""

    def test_bytes_that_are_not_audio_raise(self):
        with pytest.raises(av.error.FFmpegError):
            decode_pcm(b"\x00\x01\x02\x03" * 64)


class TestSplit:
    def _seconds(self, chunks):
        return [pcm_seconds(c) for c in chunks]

    def test_a_clip_within_the_limit_is_one_chunk(self):
        pcm = speech(("talk", 12)).tobytes()
        assert split_pcm(pcm) == [pcm]

    def test_no_chunk_exceeds_the_limit(self):
        pcm = speech(("talk", 110)).tobytes()  # continuous: no pause to help
        assert all(s <= MAX_CHUNK_SECONDS for s in self._seconds(split_pcm(pcm)))

    def test_chunks_reassemble_to_the_original(self):
        """Nothing dropped or duplicated at the cuts — every word is heard once."""
        pcm = speech(
            ("talk", 23), ("pause", 0.6), ("talk", 30), ("pause", 0.6), ("talk", 9)
        ).tobytes()
        assert b"".join(split_pcm(pcm)) == pcm

    def test_cuts_land_in_the_pauses(self):
        pcm = speech(("talk", 23), ("pause", 0.6), ("talk", 22), ("pause", 0.6), ("talk", 9))
        chunks = split_pcm(pcm.tobytes())

        assert len(chunks) == 3
        pause_starts = [23, 23 + 0.6 + 22]
        offset = 0.0
        for chunk, pause in zip(chunks[:-1], pause_starts, strict=True):
            offset += pcm_seconds(chunk)
            assert pause <= offset <= pause + 0.6, f"cut at {offset:.2f}s, pause at {pause}s"

    def test_a_long_pause_is_still_just_a_cut(self):
        pcm = speech(("talk", 25), ("pause", 3), ("talk", 10)).tobytes()
        chunks = split_pcm(pcm)
        assert len(chunks) == 2
        assert 25 <= pcm_seconds(chunks[0]) <= 28


class TestWav:
    def test_wav_is_16khz_mono_16bit_and_lossless(self):
        pcm = silence(1).tobytes()
        with wave.open(io.BytesIO(to_wav(pcm))) as w:
            assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (SAMPLE_RATE, 1, 2)
            assert w.readframes(w.getnframes()) == pcm
