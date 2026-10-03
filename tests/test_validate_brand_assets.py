from __future__ import annotations

import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from scripts.validate_brand_assets import validate


def _png_chunk(kind: bytes, content: bytes) -> bytes:
    chunk = kind + content
    return (
        struct.pack(">I", len(content))
        + chunk
        + struct.pack(">I", zlib.crc32(chunk) & 0xFFFFFFFF)
    )


def _png(width: int, height: int) -> bytes:
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    row = b"\x00" + bytes(width * 4)
    image_data = zlib.compress(row * height)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", image_data)
        + _png_chunk(b"IEND", b"")
    )


class BrandAssetValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        (self.root / "assets/brand/png").mkdir(parents=True)
        (self.root / "assets/brand/svg").mkdir(parents=True)
        (self.root / "assets/brand/png/t-primary-16.png").write_bytes(_png(16, 16))
        (self.root / "assets/brand/svg/t-primary.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'viewBox="0 0 1024 1024"></svg>\n',
            encoding="utf-8",
        )
        (self.root / "README.md").write_text(
            "# Fixture\n\n"
            '<img src="assets/brand/png/t-primary-16.png" alt="T mark">\n'
            "![Master](assets/brand/svg/t-primary.svg)\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_valid_local_images_and_exports_pass(self) -> None:
        self.assertEqual(validate(self.root), [])

    def test_missing_html_image_reference_fails_with_location(self) -> None:
        (self.root / "README.md").write_text(
            '# Fixture\n\n<img src="assets/brand/png/missing-16.png">\n',
            encoding="utf-8",
        )

        errors = validate(self.root)

        self.assertTrue(
            any(
                "README.md:3" in error
                and "assets/brand/png/missing-16.png" in error
                and "does not exist" in error
                for error in errors
            ),
            errors,
        )

    def test_missing_markdown_image_reference_fails(self) -> None:
        (self.root / "README.md").write_text(
            "# Fixture\n\n![Missing](missing.png)\n",
            encoding="utf-8",
        )

        errors = validate(self.root)

        self.assertTrue(
            any(
                "README.md:3" in error and "missing.png" in error
                for error in errors
            ),
            errors,
        )

    def test_missing_reference_style_image_fails_with_location(self) -> None:
        (self.root / "README.md").write_text(
            "# Fixture\n\n![Logo][logo]\n\n[logo]: missing.png\n",
            encoding="utf-8",
        )

        errors = validate(self.root)

        self.assertTrue(
            any(
                "README.md:3" in error
                and "missing.png" in error
                and "does not exist" in error
                for error in errors
            ),
            errors,
        )

    def test_valid_reference_style_image_passes(self) -> None:
        (self.root / "README.md").write_text(
            "# Fixture\n\n![Logo][Primary Logo]\n\n"
            "[ primary   logo ]: assets/brand/png/t-primary-16.png\n",
            encoding="utf-8",
        )

        self.assertEqual(validate(self.root), [])

    def test_png_dimensions_must_match_filename_size(self) -> None:
        (self.root / "assets/brand/png/t-primary-32.png").write_bytes(_png(16, 16))

        errors = validate(self.root)

        self.assertTrue(
            any(
                "t-primary-32.png" in error
                and "filename says 32x32 px" in error
                and "header is 16x16 px" in error
                for error in errors
            ),
            errors,
        )

    def test_malformed_svg_xml_fails(self) -> None:
        (self.root / "assets/brand/svg/broken.svg").write_text(
            "<svg>\n", encoding="utf-8"
        )

        errors = validate(self.root)

        self.assertTrue(
            any(
                "broken.svg" in error and "malformed SVG XML" in error
                for error in errors
            ),
            errors,
        )

    def test_svg_viewbox_must_match_documented_export(self) -> None:
        (self.root / "assets/brand/svg/t-primary.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'viewBox="0 0 1024 1000"></svg>\n',
            encoding="utf-8",
        )

        errors = validate(self.root)

        self.assertTrue(
            any(
                "t-primary.svg" in error
                and "0 0 1024 1024" in error
                for error in errors
            ),
            errors,
        )


if __name__ == "__main__":
    unittest.main()
