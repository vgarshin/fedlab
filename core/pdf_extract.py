"""Stage 2 core: text per slide + technical metrics from a PDF (PyMuPDF), no LLM.

Pure functions over ``bytes``; CPU-bound, so callers run them in a thread.

Counting rule (documented because the lab rule is "10 000 chars with spaces"):
page text is whitespace-normalised (runs of spaces/newlines -> one space,
trimmed); ``chars_with_spaces`` is the sum of those lengths over all slides.
"""
from __future__ import annotations

import re
from collections import Counter

import fitz  # PyMuPDF

from core.config import ProcessingCfg
from core.injection import has_hidden_text, scan_text
from core.schemas import PageText, Precheck, TechMetrics

URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"')\]]+", re.IGNORECASE)
CID_RE = re.compile(r"\(cid:\d+\)")
# Phrases meaning "the substance is somewhere else" (prompt section 10: not counted).
EXTERNAL_REF_RE = re.compile(
    # 1. a document / design / cloud-drive link: the substance is kept elsewhere
    r"drive\.google|docs\.google|disk\.yandex|yadi\.sk|яндекс[\s.]?диск|google\s*(?:drive|docs|sheets|disk)|dropbox|figma\.com|miro\.com|notion\.(?:so|site)|"
    # 2. "details / calculations / materials / data ... at the link / by QR": a pointer word shortly BEFORE "по ссылке / по QR"
    r"(?:подробн\w*|расч[её]т\w*|материал\w*|прототип\w*|полн\w+\s+(?:верси\w+|таблиц\w+)|детал\w+|данные|информаци\w+|доступн\w+|смотр\w+|см\.)"
    r"\W+(?:\w+\W+){0,6}?(?:по\s+(?:ссылк\w*|qr)|в\s+qr|qr[\s-]?код\w*)|"
    # ... or the pointer word right AFTER it
    r"по\s+(?:ссылк\w*|qr[\w-]*)\W+(?:\w+\W+){0,3}?(?:подробн|расч[её]т|материал|прототип|демо)|"
    r"ссылк\w+\s+на\s+(?:презентаци|прототип|видео|демо|файл|документ|excel|репозитори)|"
    # 3. attached / separate file
    r"в\s+приложенн\w+\s+(?:файл|excel|документ|таблиц)|отдельн\w+\s+(?:файл|документ|таблиц\w+|презентаци\w+)|приложен\w*\s+(?:файл|excel)|"
    r"см\.\s*(?:файл|приложени)",
    re.IGNORECASE,
)
# NOT matched on purpose: a bank-product feature described in the work ("взнос по ссылке", "оплата по QR-коду", "облачное хранилище").
STYLE_SPLIT_RE = re.compile(r"[-,+]")
IGNORED_FONT_SHARE = 0.01  # symbol fonts (Wingdings...) with <1% of text do not count as a typeface


def normalise(text: str) -> str:
    return " ".join(text.split())


def font_family(name: str) -> str:
    """'ABCDEF+Calibri-BoldItalic' -> 'Calibri'; 'Arial-BoldMT' -> 'Arial'."""
    name = name.split("+", 1)[-1]
    base = STYLE_SPLIT_RE.split(name)[0]
    for suffix in ("MT", "PS"):
        if base.endswith(suffix) and len(base) > len(suffix) + 2:
            base = base[: -len(suffix)]
    return base


def garbled_ratio(text: str) -> float:
    visible = [c for c in text if not c.isspace()]
    if not visible:
        return 0.0
    bad = sum(1 for c in visible if c == "�" or "" <= c <= "" or (ord(c) < 32))
    bad += sum(len(m.group()) for m in CID_RE.finditer(text))
    return min(1.0, bad / len(visible))


def _detect_qr(page: fitz.Page) -> bool:
    try:
        import cv2
        import numpy as np
    except ImportError:  # pragma: no cover
        return False
    pix = page.get_pixmap(dpi=150, colorspace=fitz.csGRAY)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    found, _, points, _ = cv2.QRCodeDetector().detectAndDecodeMulti(img)
    return bool(found) and points is not None


def extract(pdf: bytes, cfg: ProcessingCfg) -> tuple[list[PageText], TechMetrics]:
    doc = fitz.open(stream=pdf, filetype="pdf")
    pages: list[PageText] = []
    size_chars: Counter[float] = Counter()
    fam_chars: Counter[str] = Counter()
    overflow_pages: list[int] = []
    overflow_spans = 0
    urls: list[str] = []
    qr_pages: list[int] = []
    ext_pages: list[int] = []
    hidden_pages: list[int] = []
    images_total = 0
    all_text: list[str] = []

    for i, page in enumerate(doc, start=1):
        text = normalise(page.get_text("text"))
        images = len(page.get_images(full=True))
        images_total += images
        pages.append(PageText(
            number=i, text=text, chars=len(text), has_images=images > 0,
            empty=len(text) < cfg.empty_slide_min_chars, garbled=garbled_ratio(text) > cfg.max_garbled_ratio,
        ))
        all_text.append(text)

        rect = page.rect
        page_over = 0
        page_spans: list[dict] = []
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    page_spans.append(span)
                    n = len(span["text"].strip())
                    if not n:
                        continue
                    size_chars[round(span["size"] * 2) / 2] += n
                    fam_chars[font_family(span["font"])] += n
                    x0, y0, x1, y1 = span["bbox"]
                    if x0 < rect.x0 - 2 or y0 < rect.y0 - 2 or x1 > rect.x1 + 2 or y1 > rect.y1 + 2:
                        page_over += 1
        if has_hidden_text(page, page_spans):
            hidden_pages.append(i)
        if page_over:
            overflow_spans += page_over
            overflow_pages.append(i)

        urls += [m.group().rstrip(".,;:") for m in URL_RE.finditer(text)]
        urls += [lk["uri"] for lk in page.get_links() if lk.get("uri", "").startswith(("http", "www"))]
        if EXTERNAL_REF_RE.search(text):
            ext_pages.append(i)
        if images and _detect_qr(page):
            qr_pages.append(i)

    full = " ".join(all_text)
    total_fam = sum(fam_chars.values()) or 1
    families = sorted(f for f, n in fam_chars.items() if n / total_fam >= IGNORED_FONT_SHARE)
    metrics = TechMetrics(
        slide_count=len(pages),
        chars_with_spaces=sum(p.chars for p in pages),
        chars_without_spaces=sum(len(p.text.replace(" ", "")) for p in pages),
        main_font_size_pt=size_chars.most_common(1)[0][0] if size_chars else None,
        font_families=families, font_family_count=len(families),
        overflow_spans=overflow_spans, overflow_pages=overflow_pages,
        empty_slides=[p.number for p in pages if p.empty],
        image_count=images_total,
        urls=sorted(set(urls)), qr_pages=qr_pages, external_ref_pages=ext_pages,
        garbled_ratio=round(garbled_ratio(full), 4),
        hidden_text_pages=hidden_pages,
        injection_hits=scan_text([(p.number, p.text) for p in pages]),
    )
    metrics.over_char_limit = metrics.chars_with_spaces > cfg.char_limit
    metrics.text_source = "text_layer" if metrics.chars_with_spaces else "none"
    metrics.no_text_slides = pages_needing_ocr(pages)
    metrics.machine_readable = is_machine_readable(metrics, cfg)
    return pages, metrics


def is_machine_readable(m: TechMetrics, cfg: ProcessingCfg) -> bool:
    return m.chars_with_spaces >= cfg.min_text_chars_machine_readable and m.garbled_ratio <= cfg.max_garbled_ratio


def pages_needing_ocr(pages: list[PageText]) -> list[int]:
    """Slides with no usable text of their own: empty (or almost) or mostly unreadable symbols."""
    return [p.number for p in pages if p.empty or p.garbled]


def apply_ocr(pages: list[PageText], texts: dict[int, str], metrics: TechMetrics, cfg: ProcessingCfg) -> None:
    """Replace the text of the OCR'd slides and recompute text-derived metrics in place.
    Slides that keep their own text layer are untouched; font metrics are kept when any such slide exists
    (OCR gives no font data and the prompt forbids inventing it). ``no_text_slides`` is NOT recomputed:
    it records the original text-layer state, which is what the reviewer flag is about."""
    for p in pages:
        if p.number in texts:
            p.text, p.source = normalise(texts[p.number]), "ocr"
            p.chars, p.empty = len(p.text), len(p.text) < cfg.empty_slide_min_chars
            p.garbled = garbled_ratio(p.text) > cfg.max_garbled_ratio
    metrics.chars_with_spaces = sum(p.chars for p in pages)
    metrics.chars_without_spaces = sum(len(p.text.replace(" ", "")) for p in pages)
    metrics.empty_slides = [p.number for p in pages if p.empty]
    metrics.over_char_limit = metrics.chars_with_spaces > cfg.char_limit
    metrics.garbled_ratio = round(garbled_ratio(" ".join(p.text for p in pages)), 4)
    metrics.ocr_pages = sorted(texts)
    metrics.injection_hits = scan_text([(p.number, p.text) for p in pages])
    own_text = any(p.source == "text_layer" and not p.empty for p in pages)
    metrics.text_source = "mixed" if own_text and texts else ("ocr" if texts else metrics.text_source)
    metrics.machine_readable = is_machine_readable(metrics, cfg)
    if not own_text:
        metrics.main_font_size_pt, metrics.font_families, metrics.font_family_count = None, [], 0


def render_page_png(pdf: bytes, number: int, dpi: int) -> bytes:
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        return doc[number - 1].get_pixmap(dpi=dpi).tobytes("png")


ANON_RE = re.compile(r"^(anonymous|аноним\w*|без\s+названия|unknown|n/?a)$", re.IGNORECASE)


def precheck(*, is_pdf: bool, team_name: str, metrics: TechMetrics | None, cfg: ProcessingCfg,
             ocr_attempted: bool = False) -> Precheck:
    """Technical filter from the lab rules; a FAIL here means 0 points and NO LLM call."""
    if not is_pdf:
        return Precheck(status="FAIL", code="NOT_PDF", reason="Submission is not a PDF file")
    if not re.search(r"\w", team_name, re.UNICODE) or ANON_RE.match(team_name.strip()):
        return Precheck(status="FAIL", code="ANONYMOUS_WORK", reason="Team name / author is not specified")
    assert metrics is not None
    if not metrics.machine_readable:
        why = "no usable text layer" if metrics.chars_with_spaces < cfg.min_text_chars_machine_readable \
            else f"text layer is garbled (ratio {metrics.garbled_ratio})"
        return Precheck(status="FAIL", code="NOT_MACHINE_READABLE",
                        reason=why + (" even after OCR" if ocr_attempted else "; OCR is disabled for this run"))
    warns = []
    if metrics.over_char_limit:
        warns.append(f"OVER_CHAR_LIMIT: {metrics.chars_with_spaces} > {cfg.char_limit}")
    if metrics.overflow_spans:
        warns.append(f"TEXT_OVERFLOW: {metrics.overflow_spans} spans on pages {metrics.overflow_pages}")
    if metrics.font_family_count > 3:
        warns.append(f"MANY_FONT_FAMILIES: {metrics.font_family_count}")
    if metrics.no_text_slides and len(metrics.no_text_slides) < metrics.slide_count:
        # some slides have a text layer and some do not (image-only or garbled): informational only,
        # the grader never sees this warning (filtered in core/stages/grading.py)
        recovered = [n for n in metrics.no_text_slides if n in metrics.ocr_pages]
        missing = [n for n in metrics.no_text_slides if n not in metrics.ocr_pages]
        msg = f"PARTIALLY_READABLE: slide(s) {metrics.no_text_slides} have no usable text layer"
        if recovered:
            msg += f"; OCR recovered {recovered}"
        if missing:
            msg += f"; no text for {missing}"
        warns.append(msg)
    if metrics.ocr_pages:
        warns.append(f"OCR_PAGES: text of slide(s) {metrics.ocr_pages} was recognised by OCR")
    if metrics.qr_pages:
        warns.append(f"QR_CODE: pages {metrics.qr_pages}")
    if metrics.external_ref_pages:
        warns.append(f"EXTERNAL_REFERENCE: pages {metrics.external_ref_pages}")
    if metrics.hidden_text_pages:
        warns.append(f"HIDDEN_TEXT: invisible or tiny text on pages {metrics.hidden_text_pages}")
    if metrics.injection_hits:
        slides = sorted({h["slide"] for h in metrics.injection_hits})
        pats = sorted({h["pattern"] for h in metrics.injection_hits})
        warns.append(f"INJECTION_SUSPECTED: pages {slides} ({', '.join(pats)})")
    return Precheck(status="PASS", warnings=warns)
