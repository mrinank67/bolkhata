"""Image upload sanitization.

image_utils is a security boundary: it accepts arbitrary bytes from any signed-in
client and its output is written to Firebase Storage. The properties asserted here
(format allowlist, header-only bomb check, always-re-encode, metadata stripped)
are the controls the module's own docstring claims — this pins them.
"""

import io

import pytest
from PIL import Image

from image_utils import (
    ALLOWED_FORMATS,
    MAIN_MAX_EDGE,
    MAX_PIXELS,
    MIN_EDGE,
    THUMB_MAX_EDGE,
    ImageRejected,
    _sniff,
    process_item_image,
)


def make_image(fmt="JPEG", size=(800, 600), mode="RGB", color=(120, 90, 200), **save_kw) -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, format=fmt, **save_kw)
    return buf.getvalue()


class TestSniff:
    @pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
    def test_accepts_allowlisted_formats(self, fmt):
        _sniff(make_image(fmt=fmt))  # must not raise

    @pytest.mark.parametrize(
        ("label", "data"),
        [
            ("svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'),
            ("html", b"<!doctype html><html><body>hi</body></html>"),
            ("gif", b"GIF89a" + b"\x00" * 32),
            ("bmp", b"BM" + b"\x00" * 32),
            ("heic", b"\x00\x00\x00\x18ftypheic" + b"\x00" * 16),
            ("pdf", b"%PDF-1.7\n" + b"\x00" * 32),
            ("empty", b""),
            ("random", b"\x00\x01\x02\x03"),
        ],
    )
    def test_rejects_everything_else(self, label, data):
        with pytest.raises(ImageRejected):
            _sniff(data)

    def test_rejects_riff_that_is_not_webp(self):
        """RIFF alone is not enough — a WAV file starts the same way."""
        with pytest.raises(ImageRejected):
            _sniff(b"RIFF" + b"\x00\x00\x00\x00" + b"WAVE" + b"\x00" * 16)

    def test_content_type_is_never_consulted(self):
        """Sniffing is byte-based; a truthful-looking filename cannot help."""
        with pytest.raises(ImageRejected):
            _sniff(b"not an image at all, but I promise it is photo.jpg")


class TestProcessItemImage:
    def test_returns_webp_regardless_of_input_format(self):
        for fmt in ALLOWED_FORMATS:
            main, thumb, _, _ = process_item_image(make_image(fmt=fmt))
            assert Image.open(io.BytesIO(main)).format == "WEBP"
            assert Image.open(io.BytesIO(thumb)).format == "WEBP"

    def test_downscales_to_the_main_edge_cap(self):
        main, _, width, height = process_item_image(make_image(size=(4000, 3000)))
        assert max(width, height) == MAIN_MAX_EDGE
        assert (width, height) == Image.open(io.BytesIO(main)).size

    def test_thumbnail_is_capped_separately(self):
        _, thumb, _, _ = process_item_image(make_image(size=(4000, 3000)))
        assert max(Image.open(io.BytesIO(thumb)).size) == THUMB_MAX_EDGE

    def test_small_images_are_not_upscaled(self):
        _, _, width, height = process_item_image(make_image(size=(100, 80)))
        assert (width, height) == (100, 80)

    def test_rejects_images_below_the_minimum_edge(self):
        with pytest.raises(ImageRejected, match="too small"):
            process_item_image(make_image(size=(MIN_EDGE - 1, 100)))

    def test_accepts_exactly_the_minimum_edge(self):
        process_item_image(make_image(size=(MIN_EDGE, MIN_EDGE)))

    def test_rejects_decompression_bomb_from_the_header_alone(self):
        """A tiny file declaring a huge canvas must never be decoded.

        Pillow's own guard only raises above 2x MAX_IMAGE_PIXELS; the explicit
        width*height check is what covers the 1x-2x band.
        """
        side = int(MAX_PIXELS**0.5) + 500
        bomb = make_image(fmt="PNG", size=(side, side), color=(0, 0, 0))
        assert len(bomb) < 1_000_000, "bomb fixture should be small on disk"

        with pytest.raises(ImageRejected, match="resolution is too large"):
            process_item_image(bomb)

    def test_rejects_truncated_file(self):
        data = make_image(fmt="JPEG", size=(800, 600))
        with pytest.raises(ImageRejected):
            process_item_image(data[: len(data) // 3])

    def test_rejects_valid_header_with_corrupt_body(self):
        """Header parses, pixel stream does not.

        Uses noise so the PNG has a large IDAT to corrupt — a solid-colour image
        compresses to almost nothing and its tail is mostly padding, which
        Pillow tolerates.
        """
        import random

        random.seed(7)
        img = Image.new("RGB", (400, 400))
        img.putdata([(random.randrange(256),) * 3 for _ in range(400 * 400)])
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        data = bytearray(buf.getvalue())
        midpoint = len(data) // 2
        data[midpoint : midpoint + 2000] = b"\xff" * 2000

        with pytest.raises(ImageRejected, match="corrupt"):
            process_item_image(bytes(data))

    def test_transparency_is_flattened_onto_white(self):
        buf = io.BytesIO()
        Image.new("RGBA", (200, 200), (255, 0, 0, 0)).save(buf, format="PNG")
        main, _, _, _ = process_item_image(buf.getvalue())

        out = Image.open(io.BytesIO(main))
        assert out.mode == "RGB", "alpha must be gone, not carried into WebP"
        # Fully transparent red over white composites to white.
        r, g, b = out.convert("RGB").getpixel((100, 100))
        assert (r, g, b) == (255, 255, 255)

    def test_palette_mode_is_normalized(self):
        buf = io.BytesIO()
        Image.new("RGB", (300, 300), (10, 200, 60)).convert("P").save(buf, format="PNG")
        main, _, _, _ = process_item_image(buf.getvalue())
        assert Image.open(io.BytesIO(main)).mode == "RGB"

    def test_exif_metadata_does_not_survive_re_encoding(self):
        """A phone stamps GPS coordinates onto photos taken inside the shop."""
        exif = Image.Exif()
        exif[0x010F] = "SecretCameraMake"
        buf = io.BytesIO()
        Image.new("RGB", (600, 400), (30, 30, 30)).save(buf, format="JPEG", exif=exif)
        original = buf.getvalue()
        assert b"SecretCameraMake" in original

        main, thumb, _, _ = process_item_image(original)
        assert b"SecretCameraMake" not in main
        assert b"SecretCameraMake" not in thumb
        assert not Image.open(io.BytesIO(main)).getexif()

    def test_trailing_polyglot_data_is_dropped(self):
        """Bytes appended after a valid image must not be passed through."""
        payload = b"<?php system($_GET['c']); ?>"
        main, _, _, _ = process_item_image(make_image(fmt="PNG", size=(300, 300)) + payload)
        assert payload not in main

    @pytest.mark.slow
    def test_main_image_stays_under_the_storage_ceiling_for_noisy_input(self):
        """High-entropy photos are the case that blows the storage budget."""
        import random

        random.seed(1234)
        noisy = Image.new("RGB", (2000, 1500))
        noisy.putdata(
            [
                (random.randrange(256), random.randrange(256), random.randrange(256))
                for _ in range(2000 * 1500)
            ]
        )
        buf = io.BytesIO()
        noisy.save(buf, format="PNG")

        main, _, _, _ = process_item_image(buf.getvalue())
        # The quality-fallback path caps the worst case at roughly half the
        # naive q80 size; assert it produced *something* well under 300 KB.
        assert len(main) < 300 * 1024
