"""Stage 2 helper: cheap, deterministic signs that a work tries to steer the grader (prompt injection).

Findings are warnings for the reviewer, never score changes. Matches are reported by pattern id and slide
number only: the hostile text itself is not repeated in warnings.
"""
from __future__ import annotations

import re

import fitz  # PyMuPDF

INVISIBLE_RE = re.compile("[­​-‏‪-‮⁠-⁤﻿]")
# Latin <-> Cyrillic look-alikes, so that "Ignоre" (Cyrillic о) and "игнoрируй" (Latin o) still match
_CYR_TO_LAT = str.maketrans("аеорсухАЕОРСТХВКМН", "aeopcyxAEOPCTXBKMH")
_LAT_TO_CYR = str.maketrans("aeopcyxAEOPCTXBKMH", "аеорсухАЕОРСТХВКМН")

_I = re.IGNORECASE
# (pattern id, language of the variant to match on, regex)
PATTERNS: list[tuple[str, str, re.Pattern]] = [
    ("ignore_instructions", "ru", re.compile(r"(игнорир|не\s+учитывай|отбрось|забудь)\w*\s+(все\s+|любые\s+|свои\s+|предыдущие\s+|прошлые\s+|эти\s+)*"
                                             r"(инструкц|правил|критери|указани|роль)", _I)),
    ("ignore_instructions", "en", re.compile(r"(ignore|disregard|forget)\s+(all\s+|any\s+|the\s+|your\s+)*(previous|prior|above|earlier)?\s*"
                                             r"(instructions?|rules|criteria|prompts?|guidelines)", _I)),
    ("score_demand", "ru", re.compile(r"(постав\w*|выстав\w*|присво\w*|оцени\w*|дай)\s[^.\n]{0,50}?(10\s*(из|/)\s*10|100\s*(из|/)\s*100|"
                                      r"\b10\s+балл|\b100\s+балл|максимальн\w+\s+(балл|оценк)|все\s+критери\w+\s+[^.\n]{0,20}10)", _I)),
    ("score_demand", "en", re.compile(r"(score|grade|rate|give|set)\w*\s[^.\n]{0,50}?(10\s*/\s*10|=\s*10\b|\b100\s*/\s*100|full\s+marks|maximum\s+(score|marks|points))", _I)),
    ("role_override", "ru", re.compile(r"ты\s+теперь|новая\s+задача\s+для\s+модел|с\s+этого\s+момента\s+ты", _I)),
    ("role_override", "en", re.compile(r"you\s+are\s+now|new\s+instructions?\s*:|from\s+now\s+on\s+you", _I)),
    ("fake_system_message", "ru", re.compile(r"системн\w+\s+(сообщени|промпт|инструкц)|system\s+(message|prompt|override)", _I)),
    ("fake_system_message", "en", re.compile(r"\bsystem\s*(message|prompt|override|:)|\[\s*system\s*\]", _I)),
    ("prompt_leak_request", "ru", re.compile(r"(выведи|покажи|раскрой|напечатай|повтори)\s[^.\n]{0,40}(промпт|инструкци)", _I)),
    ("prompt_leak_request", "en", re.compile(r"(reveal|print|show|repeat|output)\s[^.\n]{0,30}(system\s+prompt|instructions)", _I)),
    ("delimiter_break", "ru", re.compile(r"<<<\s*(начало|конец)\s+текста", _I)),
    ("addressed_to_ai", "ru", re.compile(r"для\s+(проверяющ\w+\s+|оценивающ\w+\s+)?модел\w+|для\s+оценщика\s*\(\s*ии\s*\)|обращени\w+\s+к\s+(модел|ии)", _I)),
    ("addressed_to_ai", "en", re.compile(r"(note|message|attention|important)\s+(to|for)\s+(the\s+)?(ai|model|grader|llm|detector|evaluator)", _I)),
    ("fake_precheck_or_verdict", "ru", re.compile(r"технически\w+\s+фильтр\s*:?\s*pass|ai[-\s]вероятность\s*:?\s*0\s*%|одобрен\w*\s+(куратор|жюри|организатор)", _I)),
]


def _variants(text: str) -> dict[str, str]:
    clean = INVISIBLE_RE.sub("", text)
    return {"ru": clean.translate(_LAT_TO_CYR), "en": clean.translate(_CYR_TO_LAT)}


def scan_text(slides: list[tuple[int, str]]) -> list[dict]:
    """[(slide number, text)] -> [{"slide": n, "pattern": id}] (one entry per slide and pattern)."""
    hits: list[dict] = []
    for number, text in slides:
        if not text:
            continue
        v = _variants(text)
        seen: set[str] = set()
        for pid, lang, rx in PATTERNS:
            if pid not in seen and rx.search(v[lang]):
                seen.add(pid)
                hits.append({"slide": number, "pattern": pid})
    return hits


TINY_PT = 3.0
LIGHT = 0.93   # every RGB channel above this = "white-ish"


def _light(rgb: int) -> bool:
    return all(((rgb >> s) & 255) / 255 >= LIGHT for s in (16, 8, 0))


def has_hidden_text(page: fitz.Page, spans: list[dict]) -> bool:
    """Text a human cannot see but the text layer carries: tiny glyphs, or white-ish glyphs with nothing
    darker (a filled shape or an image) behind them. Legit white text on a dark band is not reported."""
    suspects = [s for s in spans if s["text"].strip() and (s["size"] < TINY_PT or _light(s["color"]))]
    if not suspects:
        return False
    if any(s["size"] < TINY_PT for s in suspects):
        return True
    backs = [d["rect"] for d in page.get_drawings() if d.get("fill") and not all(c >= LIGHT for c in d["fill"])]
    backs += [fitz.Rect(i["bbox"]) for i in page.get_image_info()]
    for s in suspects:
        c = fitz.Rect(s["bbox"]).tl + (fitz.Rect(s["bbox"]).br - fitz.Rect(s["bbox"]).tl) * 0.5
        if not any(r.contains(c) for r in backs):
            return True
    return False
