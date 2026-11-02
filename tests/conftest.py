from __future__ import annotations

import io
import zipfile

import fitz
import pytest

from core.config import ProcessingCfg


@pytest.fixture
def proc() -> ProcessingCfg:
    return ProcessingCfg()


def make_pdf(pages: list[str], fontname: str = "helv", size: float = 14, overflow: bool = False) -> bytes:
    doc = fitz.open()
    for text in pages:
        page = doc.new_page(width=800, height=450)
        if text:
            page.insert_textbox(fitz.Rect(30, 30, 770, 420), text, fontname=fontname, fontsize=size)
        if overflow:
            page.insert_text((780, 440), "overflowing text goes out", fontname=fontname, fontsize=size)
    out = doc.tobytes()
    doc.close()
    return out


def make_scan_pdf(n: int = 2) -> bytes:
    """Image-only PDF (no text layer), like a scan."""
    doc = fitz.open()
    for _ in range(n):
        page = doc.new_page(width=800, height=450)
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 100), False)
        pix.clear_with(200)
        page.insert_image(fitz.Rect(50, 50, 400, 250), pixmap=pix)
    out = doc.tobytes()
    doc.close()
    return out


def make_zip(teams: dict[str, tuple[str, bytes]]) -> bytes:
    """teams: '<Team>_<sid>' -> (filename, content) in the Moodle layout."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for folder, (fname, blob) in teams.items():
            zf.writestr(f"lab-assign-123/{folder}_assignsubmission_file/{fname}", blob)
        zf.writestr("__MACOSX/junk", b"x")
    return buf.getvalue()


def make_mixed_pdf() -> bytes:
    """Slide 1 has a text layer, slide 2 is an image only (a scan), slide 3 has text again."""
    doc = fitz.open()
    for i in range(3):
        page = doc.new_page(width=800, height=450)
        if i == 1:
            pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 100), False)
            pix.clear_with(200)
            page.insert_image(fitz.Rect(50, 50, 400, 250), pixmap=pix)
        else:
            page.insert_textbox(fitz.Rect(30, 30, 770, 420), "Real text layer on this slide. " * 6, fontname="helv", fontsize=14)
    out = doc.tobytes()
    doc.close()
    return out
