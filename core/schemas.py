"""Pydantic models shared by API, worker and stages."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class RunConfig(BaseModel):
    """Per-run options. Models and prompts are NOT stored here: they come from the Settings page
    (default model + default prompt per stage) and are recorded in every result's ``meta``."""

    ocr_enabled: bool = False        # opt-in OCR for PDFs without a text layer
    anonymize: bool = False          # stage 2b (planned)


class PageText(BaseModel):
    number: int          # 1-based slide number
    text: str
    chars: int           # normalised chars with spaces
    source: Literal["text_layer", "ocr"] = "text_layer"
    has_images: bool = False
    empty: bool = False
    garbled: bool = False            # the page text is mostly unreadable symbols: needs OCR as much as an empty page


class TechMetrics(BaseModel):
    """Precomputed technical facts passed to the LLM (names follow the grading prompt)."""

    is_pdf: bool = True
    machine_readable: bool = True
    text_source: Literal["text_layer", "ocr", "mixed", "none"] = "text_layer"
    slide_count: int = 0
    chars_with_spaces: int = 0
    chars_without_spaces: int = 0
    over_char_limit: bool = False
    main_font_size_pt: float | None = None
    font_families: list[str] = Field(default_factory=list)
    font_family_count: int = 0
    overflow_spans: int = 0
    overflow_pages: list[int] = Field(default_factory=list)
    empty_slides: list[int] = Field(default_factory=list)
    image_count: int = 0
    urls: list[str] = Field(default_factory=list)
    qr_pages: list[int] = Field(default_factory=list)
    external_ref_pages: list[int] = Field(default_factory=list)
    garbled_ratio: float = 0.0
    ocr_pages: list[int] = Field(default_factory=list)   # slides whose text came from OCR
    no_text_slides: list[int] = Field(default_factory=list)  # slides whose own text layer was empty/garbled, recorded before OCR; reviewer info only
    hidden_text_pages: list[int] = Field(default_factory=list)   # invisible/tiny text in the text layer (core/injection.py)
    injection_hits: list[dict] = Field(default_factory=list)     # [{"slide": n, "pattern": id}]: reviewer hints, not shown to the grader


class Precheck(BaseModel):
    status: Literal["PASS", "FAIL"]
    code: Literal["NOT_PDF", "ANONYMOUS_WORK", "NOT_MACHINE_READABLE"] | None = None
    reason: str | None = None
    warnings: list[str] = Field(default_factory=list)


# ---------- stage 4 result (format fixed by the lab prompts, section "ФОРМАТ РЕЗУЛЬТАТА") ----------
CRITERIA = {
    "criterion_1": "Полнота и четкость решения",
    "criterion_2": "Внутренняя согласованность",
    "criterion_3": "Качество анализа",
    "criterion_4": "Практическая ценность",
    "criterion_5": "Структурированность презентации",
    "criterion_6": "Визуальное оформление презентации",
}
CONTENT_DQ_CODES = ("D1_ALL_CONTENT_EMPTY", "D2_OFF_TOPIC", "D3_NONSENSE_OR_IRRELEVANT", "D4_STRUCTURE_ONLY")


_EVIDENCE_STR_RE = re.compile(r"^\s*(?:slide|слайд\w*)\s*(\d+)\s*[:.\-–—]?\s*(.*)$", re.IGNORECASE | re.DOTALL)


class Evidence(BaseModel):
    slide: int | None = None
    description: str = ""

    @model_validator(mode="before")
    @classmethod
    def _from_plain_string(cls, v):
        """Some answers give evidence as 'slide 5: text' instead of {slide, description}: accept both."""
        if isinstance(v, str):
            m = _EVIDENCE_STR_RE.match(v)
            return {"slide": int(m[1]), "description": m[2].strip()} if m else {"description": v.strip()}
        return v


class CriterionScore(BaseModel):
    name: str = ""
    score: int = Field(ge=1, le=10)
    evidence: list[Evidence] = Field(default_factory=list)
    feedback: str = ""


# GradeResult fields the model sometimes puts INSIDE ``scores``: field -> its empty/default value
_SUMMARY_FIELDS = {
    "sum_score_60": 0,
    "total_score_100": 0,
    "general_feedback": "",
    "technical_warnings": [],
    "needs_human_review": False,
}


def _lift_misplaced_scores(raw: dict) -> dict:
    """Lift summary fields misplaced inside ``scores`` to the top level (a common LLM nesting mistake
    that once made whole teams ungradeable). A top-level value wins over a nested copy; unknown keys
    are dropped. Never a silent pass: every change is recorded in ``quality_flags``."""
    scores = raw.get("scores")
    if not isinstance(scores, dict):
        return raw
    misplaced = {k: v for k, v in scores.items() if k not in CRITERIA}
    if not misplaced:
        return raw
    raw = {**raw, "scores": {k: v for k, v in scores.items() if k in CRITERIA}}
    lifted, dropped = [], []
    for k, v in misplaced.items():
        # a nested value wins only over a missing/default top-level one; None is never lifted
        if k in _SUMMARY_FIELDS and v is not None and raw.get(k, _SUMMARY_FIELDS[k]) == _SUMMARY_FIELDS[k]:
            raw[k] = v
            lifted.append(k)
        else:
            dropped.append(k)
    flags = list(raw.get("quality_flags") or [])
    if lifted:
        flags.append("lifted_from_scores:" + ",".join(sorted(lifted)))
    if dropped:
        flags.append("dropped_scores_keys:" + ",".join(sorted(dropped)))
    raw["quality_flags"] = flags
    return raw


class GradeResult(BaseModel):
    team_name: str = ""
    status: Literal["EVALUATED", "DISQUALIFIED"]
    disqualified: bool
    disqualification_code: str | None = None
    disqualification_reason: str | None = None
    disqualification_evidence: list[Evidence] = Field(default_factory=list)
    scores: dict[str, CriterionScore] | None = None
    sum_score_60: int = 0
    total_score_100: int = 0
    general_feedback: str = ""
    technical_warnings: list[str] = Field(default_factory=list)
    needs_human_review: bool = False
    # added by code, not by the model
    quality_flags: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _lift_misplaced_scores(cls, v):
        return _lift_misplaced_scores(v) if isinstance(v, dict) else v


class AiSignal(BaseModel):
    score: float = Field(ge=0, le=1)
    evidence: str = ""


class ManipulationAttempt(BaseModel):
    """Optional semantic check of stage 3: the work tries to steer the grader. Informational, like the rest of stage 3."""
    found: bool = False
    slides: list[int] = Field(default_factory=list)
    evidence: str = ""


class AiDetectResult(BaseModel):
    ai_probability: float = Field(ge=0, le=1)
    verdict: Literal["low", "medium", "high"]
    confidence: Literal["low", "medium", "high"] = "medium"
    signals: dict[str, AiSignal] = Field(default_factory=dict)
    rationale: str = ""
    manipulation_attempt: ManipulationAttempt | None = None   # filled only by ai_detect prompt versions that ask for it
