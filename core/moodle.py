"""Moodle file-based adapters: submissions zip + grading worksheet CSV.

Interfaces ``MoodleSource`` / ``MoodleSink`` keep room for Web Services later
without touching the pipeline.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass, field
from collections.abc import Iterable
from typing import Protocol

SUBMISSION_DIR_RE = re.compile(r"^(?P<team>.*)_(?P<sid>\d+)_assignsubmission_file$")


@dataclass
class Submission:
    team: str
    submission_id: str
    rel_dir: str = ""                                # "<assignment>/<Team>_<id>_assignsubmission_file"
    files: list[str] = field(default_factory=list)   # paths relative to the tree root

    @property
    def pdfs(self) -> list[str]:
        return [f for f in self.files if f.lower().endswith(".pdf")]


class MoodleSource(Protocol):
    def submissions(self) -> list[Submission]: ...


def is_junk(path: str) -> bool:
    return path.startswith("__MACOSX/") or path.rsplit("/", 1)[-1].startswith("._")


def group_submissions(paths: Iterable[str]) -> list[Submission]:
    """Group relative file paths by '<Team>_<submissionId>_assignsubmission_file' folders."""
    found: dict[str, Submission] = {}
    for path in paths:
        parts = path.split("/")
        if is_junk(path) or len(parts) < 2:
            continue
        m = SUBMISSION_DIR_RE.match(parts[-2])
        if not m:
            continue
        rel_dir = "/".join(parts[:-1])
        sub = found.setdefault(rel_dir, Submission(m["team"], m["sid"], rel_dir))
        sub.files.append(path)
    return sorted(found.values(), key=lambda s: (s.team.casefold(), s.submission_id))


def scan_zip(zf: zipfile.ZipFile) -> list[Submission]:
    return group_submissions(i.filename for i in zf.infolist() if not i.is_dir())


def parse_worksheet(raw: bytes) -> list[dict[str, str]]:
    """Moodle offline-grading worksheet: delimiter is sniffed (comma or tab), BOM tolerated."""
    text = raw.decode("utf-8-sig", errors="replace")
    delim = "\t" if text.split("\n", 1)[0].count("\t") > text.split("\n", 1)[0].count(",") else ","
    return list(csv.DictReader(io.StringIO(text), delimiter=delim))


def norm_team(name: str) -> str:
    return " ".join(name.casefold().replace("«", '"').replace("»", '"').split())


def match_groups(team_names: list[str], rows: list[dict[str, str]]) -> dict[str, int]:
    """team name -> number of worksheet rows whose Group equals it (0 = unmatched)."""
    counts: dict[str, int] = {}
    for r in rows:
        g = norm_team(r.get("Group", ""))
        counts[g] = counts.get(g, 0) + 1
    return {t: counts.get(norm_team(t), 0) for t in team_names}
