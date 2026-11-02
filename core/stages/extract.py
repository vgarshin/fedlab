"""Stage 2: per-team text + metrics + technical pre-check. One team = one atomic unit."""
from __future__ import annotations

import asyncio

import fitz

from core import keys
from core.config import OcrCfg, ProcessingCfg
from core.manifest import hash_obj, is_done, write_manifest
from core.llm import BudgetExceeded
from core.ocr import YandexOCR
from core.pdf_extract import apply_ocr, extract, pages_needing_ocr, precheck, render_page_png
from core.schemas import PageText, Precheck, RunConfig, TechMetrics
from core.storage import Storage, put_json

STAGE = "s2_extract"
STAGE_VERSION = "4"  # v4: no_text_slides metric + PARTIALLY_READABLE warning


def config_hash(run_cfg: RunConfig, proc: ProcessingCfg, ocr: OcrCfg, ocr_max_pages: int) -> str:
    return hash_obj({"v": STAGE_VERSION, "ocr": run_cfg.ocr_enabled, "proc": proc.model_dump(),
                     "ocr_dpi": ocr.dpi if run_cfg.ocr_enabled else None,
                     "ocr_max_pages": ocr_max_pages if run_cfg.ocr_enabled else None})


async def run_extract(*, data: Storage, run_id: int, slug: str, team_name: str, file_key: str,
                      file_sha256: str, is_pdf: bool, run_cfg: RunConfig, proc: ProcessingCfg,
                      ocr_cfg: OcrCfg, ocr: YandexOCR | None, ocr_max_pages: int = 60,
                      can_spend=None) -> dict:
    """Returns a summary dict. ``{"resumed": True}`` when a matching manifest already exists."""
    inputs_hash = hash_obj({"sha": file_sha256, "team": team_name, "is_pdf": is_pdf})
    cfg_hash = config_hash(run_cfg, proc, ocr_cfg, ocr_max_pages)
    if await is_done(data, run_id, slug, STAGE, inputs_hash, cfg_hash):
        return {"resumed": True}

    pages: list[PageText] = []
    metrics: TechMetrics | None = None
    ocr_attempted = False
    paid_pages = 0
    ocr_note = ""
    if is_pdf:
        pdf = await data.get(file_key)
        if pdf is None:
            raise FileNotFoundError(file_key)
        try:
            pages, metrics = await asyncio.to_thread(extract, pdf, proc)
        except (fitz.FileDataError, fitz.EmptyFileError, RuntimeError):
            pages, metrics = [], TechMetrics(machine_readable=False, text_source="none")
        pc = precheck(is_pdf=True, team_name=team_name, metrics=metrics, cfg=proc)
        todo = pages_needing_ocr(pages)
        if run_cfg.ocr_enabled and ocr and todo:
            # OCR only the slides that have no usable text of their own (empty or garbled); slides with a text layer are kept
            if len(todo) > ocr_max_pages:
                ocr_note = f"OCR skipped: {len(todo)} slides need OCR, the limit is {ocr_max_pages} (Settings → OCR)"
            else:
                if can_spend is not None and not await can_spend(len(todo) * ocr_cfg.price_per_page_rub):
                    raise BudgetExceeded("Run budget cap would be exceeded by OCR")
                ocr_attempted = True
                texts, paid_pages = await _ocr_pages(ocr, pdf, file_sha256, todo, ocr_cfg.dpi, proc.ocr_concurrency)
                apply_ocr(pages, texts, metrics, proc)
                pc = precheck(is_pdf=True, team_name=team_name, metrics=metrics, cfg=proc, ocr_attempted=True)
        if ocr_note:
            pc.warnings.append("OCR_SKIPPED: " + ocr_note)
    else:
        pc = precheck(is_pdf=False, team_name=team_name, metrics=None, cfg=proc)

    outs = []
    for name, obj in (("s2_pages.json", [p.model_dump() for p in pages]),
                      ("s2_metrics.json", (metrics or TechMetrics(is_pdf=False, machine_readable=False,
                                                                  text_source="none")).model_dump()),
                      ("s2_precheck.json", pc.model_dump())):
        key = keys.artifact(run_id, slug, name)
        await put_json(data, key, obj)
        outs.append(key)
    await write_manifest(data, run_id, slug, STAGE, inputs_hash, cfg_hash, outs,
                         {"ocr_attempted": ocr_attempted, "precheck": pc.status})
    return {"resumed": False, "ocr_pages_paid": paid_pages, "precheck": pc.model_dump(),
            "chars_with_spaces": metrics.chars_with_spaces if metrics else None,
            "slide_count": metrics.slide_count if metrics else None}


async def _ocr_pages(ocr: YandexOCR, pdf: bytes, sha: str, numbers: list[int], dpi: int, conc: int) -> tuple[dict[int, str], int]:
    """OCR the given slides. Returns (texts, pages actually billed); each page result is cached => resumable."""
    sem = asyncio.Semaphore(conc)

    async def one(i: int) -> tuple[int, str, bool]:
        async with sem:
            png = await asyncio.to_thread(render_page_png, pdf, i, dpi)
            text, cache_hit = await ocr.page_text(sha, i, png)
            return i, text, not cache_hit

    res = await asyncio.gather(*(one(i) for i in numbers))
    return {i: t for i, t, _ in res}, sum(1 for *_, paid in res if paid)
