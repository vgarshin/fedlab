"""Object-key layout (see PLAN.md section 5). One place, so keys never drift."""
from __future__ import annotations

import re

STAGES = ("s1_ingest", "s2_extract", "s3_detect", "s4_grade", "s5_export")


def slugify(name: str) -> str:
    s = re.sub(r"[^\w\-.]+", "_", name.strip(), flags=re.UNICODE).strip("._")
    return s[:80] or "team"


def input_prefix(run_id: int) -> str:
    return f"input/{run_id}/"


def source_prefix(run_id: int) -> str:
    """Where UI uploads land before being unpacked into the canonical tree."""
    return f"{input_prefix(run_id)}_source/"


def source_zip(run_id: int) -> str:
    return f"{source_prefix(run_id)}submissions.zip"


def source_file(run_id: int, name: str) -> str:
    return f"{source_prefix(run_id)}{name}"


def canonical(run_id: int, rel_path: str) -> str:
    """Canonical input key: the Moodle tree exactly as in `cosmos`, under the run prefix:
    input/<run>/<assignment-dir>/<Team>_<id>_assignsubmission_file/<file>  (+ the worksheet CSV)."""
    return f"{input_prefix(run_id)}{rel_path}"


def result_prefix(run_id: int) -> str:
    return f"{run_id}/"


def work_prefix(run_id: int, slug: str) -> str:
    return f"_work/{run_id}/{slug}/"


def artifact(run_id: int, slug: str, name: str) -> str:
    return f"{work_prefix(run_id, slug)}{name}"


def manifest(run_id: int, slug: str, stage: str) -> str:
    return artifact(run_id, slug, f"{stage}.manifest.json")


def ocr_page(file_sha256: str, dpi: int, page: int) -> str:
    return f"_cache/ocr/{file_sha256}/page-{dpi}dpi/page_{page:04d}.json"
