"""Real, decodable audio for the tests that exercise audio_split.py.

The route tests' usual AUDIO fixture is arbitrary bytes, which PyAV cannot
decode — the route treats that as "send the upload as-is". These helpers build
the clips a browser would actually upload, so the decode and split paths run
for real.
"""

import io
import math
from array import array

import av

RATE = 16000


def tone(seconds: float, freq: float = 440.0, level: int = 8000) -> array:
    """A steady tone standing in for speech: loud, continuous, no pauses."""
    n = int(RATE * seconds)
    return array("h", (int(level * math.sin(2 * math.pi * freq * i / RATE)) for i in range(n)))


def silence(seconds: float) -> array:
    return array("h", bytes(2 * int(RATE * seconds)))


def speech(*parts: tuple[str, float]) -> array:
    """("talk", 3.0), ("pause", 0.5), … concatenated into one signal."""
    out = array("h")
    for kind, seconds in parts:
        out.extend(tone(seconds) if kind == "talk" else silence(seconds))
    return out


def encode(samples: array, fmt: str = "webm") -> bytes:
    """Encode 16 kHz mono PCM the way Chrome's MediaRecorder would: WebM/Opus."""
    codec = "libopus" if fmt == "webm" else "aac"
    buf = io.BytesIO()
    with av.open(buf, "w", format=fmt) as out:
        stream = out.add_stream(codec, rate=RATE if codec == "aac" else 48000)
        stream.layout = "mono"
        resampler = av.AudioResampler(
            format=stream.codec_context.format.name, layout="mono", rate=stream.rate
        )
        step = 960
        for pts in range(0, len(samples), step):
            chunk = samples[pts : pts + step]
            frame = av.AudioFrame(format="s16", layout="mono", samples=len(chunk))
            frame.planes[0].update(chunk.tobytes())
            frame.sample_rate = RATE
            frame.pts = pts
            for resampled in resampler.resample(frame):
                out.mux(stream.encode(resampled))
        for resampled in resampler.resample(None):
            out.mux(stream.encode(resampled))
        out.mux(stream.encode(None))
    return buf.getvalue()


def empty_container(fmt: str = "webm") -> bytes:
    """A container with a header and no audio — what a tap on the button uploads."""
    buf = io.BytesIO()
    with av.open(buf, "w", format=fmt) as out:
        stream = out.add_stream("libopus", rate=48000)
        stream.layout = "mono"
        out.start_encoding()  # writes the header, then closes with no packets
    return buf.getvalue()
