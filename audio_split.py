"""Cut a long voice clip into pieces Sarvam's REST speech-to-text will accept.

Sarvam's synchronous STT rejects anything over 30 seconds with a bare 400, and a
shopkeeper reading out five or six items routinely talks for longer than that.
Rather than make them split the list by hand, the clip is decoded here, cut into
chunks of at most MAX_CHUNK_SECONDS, and each chunk is transcribed on its own.

Each cut lands on the quietest stretch of the last few seconds before the limit
— the pause between one item and the next — so a word is never split across two
chunks, where neither half would transcribe.

Chunks are 16 kHz mono 16-bit WAV, the format Sarvam documents as its most
accurate input. A clip that already fits in one request is not touched: the
route sends the original upload exactly as it always has.
"""

import io
import wave
from array import array

import av

SAMPLE_RATE = 16000
SAMPLE_BYTES = 2  # signed 16-bit PCM

# Sarvam's hard limit is 30 s. The margin absorbs any disagreement between our
# decoder's duration and theirs, which would otherwise turn a 29.9 s chunk into
# the very 400 this module exists to prevent.
MAX_CHUNK_SECONDS = 28
# Where a cut may land: anywhere from here up to MAX_CHUNK_SECONDS into the
# current chunk. Eight seconds of speech always contains a breath between items.
MIN_CHUNK_SECONDS = 20
# A pause is judged over this span, not a single frame, so the gap between two
# syllables inside a word never passes for the gap between two words.
PAUSE_SECONDS = 0.2
FRAME_SECONDS = 0.02

# Past this, a clip is a pocket recording or a button held down by accident, not
# a command — and it would spend a Sarvam request for every chunk.
MAX_AUDIO_SECONDS = 120
# A tap on the button yields a container with a header and no audio in it.
# Sarvam answers that with a 400; it is caught here instead, for free.
MIN_AUDIO_SECONDS = 0.3


def decode_pcm(audio_bytes: bytes) -> bytes:
    """Decode any browser recording (WebM/Opus, MP4/AAC, …) to 16 kHz mono s16le.

    Returns b"" for a container that holds no audio at all. Raises whatever PyAV
    raises on audio it cannot read; the caller decides whether that is fatal.
    """
    resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    pcm = bytearray()

    def _take(frames):
        for frame in frames:
            # The plane buffer can be padded past the last sample for alignment.
            pcm.extend(bytes(frame.planes[0])[: frame.samples * SAMPLE_BYTES])

    try:
        container = av.open(io.BytesIO(audio_bytes))
    except EOFError:
        # The file ends inside its own header: a tap that recorded no audio.
        return b""

    with container:
        stream = container.streams.audio[0]
        for frame in container.decode(stream):
            _take(resampler.resample(frame))
        _take(resampler.resample(None))  # flush the resampler's tail

    return bytes(pcm)


def pcm_seconds(pcm: bytes) -> float:
    return len(pcm) / (SAMPLE_RATE * SAMPLE_BYTES)


def _quietest_cut(samples: array, lo: int, hi: int) -> int:
    """Sample index of the middle of the quietest PAUSE_SECONDS in [lo, hi)."""
    frame = int(SAMPLE_RATE * FRAME_SECONDS)
    per_pause = max(1, round(PAUSE_SECONDS / FRAME_SECONDS))

    energies = []
    for start in range(lo, hi - frame + 1, frame):
        seg = samples[start : start + frame]
        energies.append(sum(s * s for s in seg))

    if len(energies) < per_pause:
        return hi

    window = sum(energies[:per_pause])
    best, best_at = window, 0
    for i in range(per_pause, len(energies)):
        window += energies[i] - energies[i - per_pause]
        # <= so a tie goes to the later cut: fewer, longer chunks
        if window <= best:
            best, best_at = window, i - per_pause + 1

    return lo + (best_at * frame) + (per_pause * frame) // 2


def split_pcm(pcm: bytes) -> list[bytes]:
    """Cut PCM into chunks no longer than MAX_CHUNK_SECONDS, at pauses."""
    samples = array("h")
    samples.frombytes(pcm)
    total = len(samples)
    max_len = SAMPLE_RATE * MAX_CHUNK_SECONDS
    min_len = SAMPLE_RATE * MIN_CHUNK_SECONDS

    chunks = []
    start = 0
    while total - start > max_len:
        cut = _quietest_cut(samples, start + min_len, start + max_len)
        chunks.append(samples[start:cut].tobytes())
        start = cut
    chunks.append(samples[start:].tobytes())
    return chunks


def to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(SAMPLE_BYTES)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return buf.getvalue()
