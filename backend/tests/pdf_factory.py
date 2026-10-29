"""Builds PDF/DOCX fixtures for extraction tests.

Bangla PDFs use a *synthetic* TrueType font (every glyph is a plain box) generated with
fontTools, so tests need no real font file. Extraction only depends on the font's
Unicode mapping, which is what is being verified. Shaping/rendering is out of scope.
"""

import io
from collections.abc import Sequence

import pymupdf
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

SYNTHETIC_FONT_NAME = "synth"


def build_synthetic_font(text: str) -> bytes:
    chars = sorted({char for char in text if char not in "\n\r"} | {" "})
    names = {char: f"uni{ord(char):04X}" for char in chars}
    order = [".notdef", *names.values()]
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap({ord(char): name for char, name in names.items()})
    pen = TTGlyphPen(None)
    pen.moveTo((50, 0))
    pen.lineTo((50, 700))
    pen.lineTo((450, 700))
    pen.lineTo((450, 0))
    pen.closePath()
    glyph = pen.glyph()
    builder.setupGlyf(dict.fromkeys(order, glyph))
    builder.setupHorizontalMetrics(dict.fromkeys(order, (600, 0)))
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "Synthetic", "styleName": "Regular"})
    builder.setupOS2(sTypoAscender=800, usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    buffer = io.BytesIO()
    builder.save(buffer)
    return buffer.getvalue()


def make_pdf(
    pages: Sequence[str],
    *,
    metadata: dict[str, str] | None = None,
    encrypt_password: str | None = None,
) -> bytes:
    """One text block per page; an empty string produces a blank page."""
    document = pymupdf.open()
    font = build_synthetic_font("".join(pages))
    for text in pages:
        page = document.new_page()
        if not text:
            continue
        page.insert_font(fontname=SYNTHETIC_FONT_NAME, fontbuffer=font)
        for index, line in enumerate(text.split("\n")):
            if line:
                page.insert_text(
                    (72, 72 + index * 16), line, fontname=SYNTHETIC_FONT_NAME, fontsize=11
                )
    if metadata:
        document.set_metadata(metadata)
    if encrypt_password:
        return bytes(
            document.tobytes(
                encryption=pymupdf.PDF_ENCRYPT_AES_256,  # type: ignore[attr-defined]
                owner_pw=encrypt_password,
                user_pw=encrypt_password,
            )
        )
    return bytes(document.tobytes())


def _image_page(document: pymupdf.Document, text: str) -> None:
    """An image-only page: the text is drawn, rendered to pixels, and only the pixels are kept."""
    source = pymupdf.open()
    page = source.new_page(width=595, height=842)
    page.insert_textbox(pymupdf.Rect(50, 60, 545, 700), text, fontsize=22)
    pixels = page.get_pixmap(dpi=150)
    target = document.new_page(width=595, height=842)
    target.insert_image(target.rect, pixmap=pixels)


def mixed_pdf(*pages: str) -> bytes:
    """Each entry is 'scan:<text>' (image only), 'text:<text>' (text layer) or 'blank'."""
    document = pymupdf.open()
    for entry in pages:
        kind, _, content = entry.partition(":")
        if kind == "scan":
            _image_page(document, content)
        elif kind == "text":
            document.new_page().insert_text((72, 72), content)
        else:
            document.new_page()
    data = document.tobytes(deflate=True, garbage=3)
    document.close()
    return bytes(data)


def huge_scan_pdf() -> bytes:
    """One 41-inch-square page holding a small white image (to test render scaling)."""
    document = pymupdf.open()
    page = document.new_page(width=3000, height=3000)
    pixels = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 300, 300), False)
    pixels.set_rect(pixels.irect, (255,))
    page.insert_image(page.rect, pixmap=pixels)
    return bytes(document.tobytes())


def png_size(png: bytes) -> tuple[int, int]:
    pixels = pymupdf.Pixmap(png)
    return int(pixels.width), int(pixels.height)
