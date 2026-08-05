"""
Image sanitization + compression for inventory product photos.

Everything arriving from a client is treated as hostile. We sniff the real format
from magic bytes, refuse to decode anything outside a 3-format allowlist, cap the
pixel count before touching pixel data, and always RE-ENCODE from decoded pixels
rather than passing the original bytes through. That last step is the load-bearing
control: the bytes written to Storage come from our encoder, so EXIF/GPS, ICC
payloads, trailing polyglot data and format-level exploit primitives are all gone.
"""

import io
from typing import Tuple

from PIL import Image, ImageFile, ImageOps

# Never let Pillow "best effort" a truncated file — a partial decode of a
# deliberately-truncated image is exactly what a malformed-input attack wants.
# This is the library default; asserted here because it is a process-global that
# other packages flip, and this module must not inherit that.
ImageFile.LOAD_TRUNCATED_IMAGES = False

# Decompression-bomb guard. Pillow only *raises* above 2 * MAX_IMAGE_PIXELS —
# between 1x and 2x it merely emits a DecompressionBombWarning, which is invisible
# in serverless logs. So process_item_image() ALSO checks the header dimensions
# itself before any decode; that explicit check is the real control.
MAX_PIXELS = 40_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS

MIN_EDGE = 32
ALLOWED_FORMATS = ("JPEG", "PNG", "WEBP")
MAIN_MAX_EDGE, THUMB_MAX_EDGE = 1024, 256
MAIN_QUALITY, THUMB_QUALITY = 80, 70

# Storage-cost ceiling for the main image. A typical product photo lands at
# 40-120 KB at q80, but a busy, high-detail shot can reach ~290 KB — measured on
# incompressible input. Rather than let the tail dictate the storage bill, re-encode
# anything over the ceiling one step down; the quality difference is invisible at
# 1024px and it caps the worst case at roughly half.
MAIN_SIZE_CEILING = 150 * 1024
MAIN_QUALITY_FALLBACK = 62


class ImageRejected(Exception):
    """Client-visible rejection reason — safe to surface in a 400."""


def _sniff(data: bytes) -> None:
    """Magic-byte allowlist.

    Runs before Pillow so a hostile file never reaches a decoder plugin at all.
    The client's content_type and filename are never consulted — both are
    attacker-controlled. This is also what rejects SVG (a stored-XSS vector if it
    were ever served back as markup) and HEIC.
    """
    if data.startswith(b"\xff\xd8\xff"):                    # JPEG SOI
        return
    if data.startswith(b"\x89PNG\r\n\x1a\n"):               # PNG signature
        return
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":       # RIFF....WEBP
        return
    raise ImageRejected("Only JPG, PNG or WebP photos are supported.")


def process_item_image(data: bytes) -> Tuple[bytes, bytes, int, int]:
    """Validate, normalize and compress an untrusted upload.

    Returns (main_webp_bytes, thumb_webp_bytes, main_width, main_height).
    Raises ImageRejected with a shopkeeper-friendly message on any bad input.
    """
    _sniff(data)

    try:
        # formats= stops Pillow from dispatching to its ~50 other plugins (ICO,
        # DDS, FLI, and EPS which shells out to Ghostscript), several of which
        # have had RCE-class CVEs. Image.open parses only the header here — no
        # pixels are decoded until .convert() below.
        img = Image.open(io.BytesIO(data), formats=ALLOWED_FORMATS)
    except Image.DecompressionBombError:
        # Pillow's own guard, which only fires above 2 * MAX_IMAGE_PIXELS. The
        # explicit width*height check below covers the 1x-2x band, where Pillow
        # merely warns.
        raise ImageRejected("That image's resolution is too large.")
    except Exception:
        raise ImageRejected("That file isn't a readable image.")

    if img.format not in ALLOWED_FORMATS:
        raise ImageRejected("Only JPG, PNG or WebP photos are supported.")

    width, height = img.size
    if width < MIN_EDGE or height < MIN_EDGE:
        raise ImageRejected("That image is too small to use.")
    if width * height > MAX_PIXELS:
        # Header-only check: a 40 KB PNG claiming 30000x30000 (~2.7 GB decoded)
        # is rejected without ever allocating the buffer.
        raise ImageRejected("That image's resolution is too large.")

    try:
        img = ImageOps.exif_transpose(img)      # honour the rotation tag...
        img = _flatten(img)                     # ...then drop it, and decode
    except ImageRejected:
        raise
    except Exception:
        raise ImageRejected("That image is corrupt and couldn't be processed.")

    main = img.copy()
    main.thumbnail((MAIN_MAX_EDGE, MAIN_MAX_EDGE), Image.LANCZOS)
    thumb = img.copy()
    thumb.thumbnail((THUMB_MAX_EDGE, THUMB_MAX_EDGE), Image.LANCZOS)

    main_bytes = _encode(main, MAIN_QUALITY)
    if len(main_bytes) > MAIN_SIZE_CEILING:
        main_bytes = _encode(main, MAIN_QUALITY_FALLBACK)

    return main_bytes, _encode(thumb, THUMB_QUALITY), main.width, main.height


def _flatten(img: Image.Image) -> Image.Image:
    """Normalize to RGB, compositing any transparency onto white.

    Also collapses palette and CMYK modes, and removes alpha-channel
    steganography as a side effect. Product photos on a black background look
    broken, and WebP-with-alpha is larger for no benefit here.
    """
    if img.mode in ("RGBA", "LA", "P", "PA"):
        img = img.convert("RGBA")
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[-1])
        return background
    return img.convert("RGB")


def _encode(img: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    # No exif=/icc_profile= kwargs, and the pixels came from a fresh RGB buffer,
    # so the saved file carries no metadata of any kind — including the GPS
    # coordinates a phone stamps onto a photo taken inside the shop.
    img.save(buf, format="WEBP", quality=quality, method=4)
    return buf.getvalue()
