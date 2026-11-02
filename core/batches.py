"""Batches = immutable drops of Moodle files in `incoming/<batch>/` of the data bucket.

Rules (the point of this module):
  * A batch name is created ONCE ("reserved"); files are put there ONCE. Adding files to an existing batch is refused
    in the app (UI upload and name reservation) and DETECTED for the AWS CLI, which cannot be technically blocked:
    a batch that is inconsistent (2 zips, 2 CSVs, zip + unzipped works) or that changed after a run used it is marked
    and cannot be used. New files => new batch (default name YYYYMMDD-HHMM).
  * A batch holds either exactly one zip, or the unzipped Moodle folders, plus at most one worksheet CSV.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime

from core import moodle
from core.storage import Storage

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,59}$")
NAME_HELP = "3-60 characters: letters, digits, dot, dash, underscore; must start with a letter or digit"


def default_name(now: datetime) -> str:
    """YYYYMMDD-HHMM (no colon: it is awkward in file names on Windows and in URLs)."""
    return now.strftime("%Y%m%d-%H%M")


@dataclass
class BatchScan:
    prefix: str
    files: int = 0
    bytes: int = 0
    zips: list[str] = field(default_factory=list)
    csvs: list[str] = field(default_factory=list)
    unzipped_submissions: int = 0
    problems: list[str] = field(default_factory=list)
    listing_hash: str = ""


async def scan(data: Storage, prefix: str, metas: list[tuple[str, int]] | None = None) -> BatchScan:
    """Look at what is in the batch folder and say whether it is a valid single drop."""
    s = BatchScan(prefix)
    paths: list[str] = []
    listing: list[tuple[str, int]] = []
    for key, size in (metas if metas is not None else await data.list_meta(prefix)):
        rel = key[len(prefix):]
        if not rel or moodle.is_junk(rel):
            continue
        s.files += 1
        s.bytes += size
        listing.append((rel, size))
        low = rel.lower()
        if low.endswith(".zip"):
            s.zips.append(rel)
        elif low.endswith(".csv"):
            s.csvs.append(rel)
        else:
            paths.append(rel)
    s.unzipped_submissions = len(moodle.group_submissions(paths))
    s.listing_hash = hashlib.sha256(repr(sorted(listing)).encode()).hexdigest()[:24]
    if s.files == 0:
        s.problems.append("The batch is empty: upload the files first.")
    if len(s.zips) > 1:
        s.problems.append(f"The batch contains {len(s.zips)} zip files; it must contain exactly one. Create a new batch.")
    if len(s.csvs) > 1:
        s.problems.append(f"The batch contains {len(s.csvs)} CSV files; it must contain at most one. Create a new batch.")
    if s.zips and s.unzipped_submissions:
        s.problems.append("The batch contains both a zip and unzipped works; use one or the other.")
    if s.files and not s.zips and not s.unzipped_submissions:
        s.problems.append("No submissions found (no zip and no <Team>_<id>_assignsubmission_file folders).")
    return s
