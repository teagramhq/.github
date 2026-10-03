#!/usr/bin/env python3
"""Check local README images and the dimensions of Teagram brand exports."""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
import re
import struct
import sys
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path
from urllib.parse import unquote, urlsplit

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))")
DOCUMENTED_VIEWBOX = (0.0, 0.0, 1024.0, 1024.0)


class _HTMLImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: list[tuple[int, str]] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag.lower() != "img":
            return
        source = dict(attrs).get("src")
        if source is not None:
            self.references.append((self.getpos()[0], source))

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)


def _image_references(markdown: str) -> list[tuple[int, str]]:
    references = [
        (
            markdown.count("\n", 0, match.start()) + 1,
            match.group(1) or match.group(2),
        )
        for match in MARKDOWN_IMAGE.finditer(markdown)
    ]
    parser = _HTMLImageParser()
    parser.feed(markdown)
    parser.close()
    references.extend(parser.references)
    return references


def _check_markdown_images(root: Path, errors: list[str]) -> None:
    markdown_files = sorted(
        path
        for path in root.rglob("*.md")
        if path.is_file() and ".git" not in path.relative_to(root).parts
    )

    for markdown_file in markdown_files:
        try:
            content = markdown_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            errors.append(
                f"{markdown_file.relative_to(root)}: cannot read Markdown: {error}"
            )
            continue

        for line, source in _image_references(content):
            try:
                parsed = urlsplit(source)
            except ValueError as error:
                errors.append(
                    f"{markdown_file.relative_to(root)}:{line}: invalid image "
                    f"reference {source!r}: {error}"
                )
                continue
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue

            image = (markdown_file.parent / unquote(parsed.path)).resolve()
            try:
                image.relative_to(root)
            except ValueError:
                errors.append(
                    f"{markdown_file.relative_to(root)}:{line}: local image "
                    f"{source!r} resolves outside the repository"
                )
                continue

            if not image.is_file():
                errors.append(
                    f"{markdown_file.relative_to(root)}:{line}: local image "
                    f"{source!r} does not exist"
                )

def _png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as png:
        header = png.read(33)

    if len(header) < 33:
        raise ValueError("PNG header is truncated")
    if header[:8] != PNG_SIGNATURE:
        raise ValueError("invalid PNG signature")
    if header[8:12] != b"\x00\x00\x00\r" or header[12:16] != b"IHDR":
        raise ValueError("missing PNG IHDR chunk")

    expected_crc = struct.unpack(">I", header[29:33])[0]
    actual_crc = zlib.crc32(header[12:29]) & 0xFFFFFFFF
    if actual_crc != expected_crc:
        raise ValueError("invalid PNG IHDR checksum")

    width, height = struct.unpack(">II", header[16:24])
    if width == 0 or height == 0:
        raise ValueError("PNG dimensions must be positive")
    return width, height


def _check_pngs(root: Path, errors: list[str]) -> None:
    png_dir = root / "assets/brand/png"
    if not png_dir.is_dir():
        errors.append("assets/brand/png: directory is missing")
        return

    pngs = sorted(png_dir.glob("*.png"))
    for png in pngs:
        relative = png.relative_to(root)
        match = re.search(r"-(\d+)\.png$", png.name)
        if match is None:
            errors.append(
                f"{relative}: filename must end in -<size>.png to document "
                "its expected dimensions"
            )
            continue

        expected = int(match.group(1))
        try:
            dimensions = _png_dimensions(png)
        except (OSError, ValueError) as error:
            errors.append(f"{relative}: invalid PNG: {error}")
            continue

        if dimensions != (expected, expected):
            width, height = dimensions
            errors.append(
                f"{relative}: filename says {expected}x{expected} px but PNG "
                f"header is {width}x{height} px"
            )

def _check_svgs(root: Path, errors: list[str]) -> None:
    svg_dir = root / "assets/brand/svg"
    if not svg_dir.is_dir():
        errors.append("assets/brand/svg: directory is missing")
        return

    svgs = sorted(svg_dir.glob("*.svg"))
    for svg in svgs:
        relative = svg.relative_to(root)
        try:
            document = ET.parse(svg)
        except (ET.ParseError, OSError) as error:
            errors.append(f"{relative}: malformed SVG XML: {error}")
            continue

        element = document.getroot()
        if element.tag.rsplit("}", 1)[-1] != "svg":
            errors.append(f"{relative}: XML root element must be <svg>")
            continue

        view_box = element.attrib.get("viewBox")
        try:
            values = tuple(
                float(value) for value in re.split(r"[\s,]+", view_box.strip())
            )
        except (AttributeError, ValueError):
            values = ()

        if values != DOCUMENTED_VIEWBOX:
            errors.append(
                f"{relative}: viewBox {view_box!r} does not match the documented "
                "0 0 1024 1024 export"
            )

def validate(root: Path) -> list[str]:
    root = root.resolve()
    if not root.is_dir():
        return [f"{root}: repository root is not a directory"]

    errors: list[str] = []
    _check_markdown_images(root, errors)
    _check_pngs(root, errors)
    _check_svgs(root, errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root to validate",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    errors = validate(root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(f"Content validation passed for {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
