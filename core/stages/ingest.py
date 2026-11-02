"""Stage 1: bring a Moodle export into the canonical tree and register the teams.

Canonical tree (identical to `cosmos`, with the run kept in the path):
    input/<run>/<assignment-dir>/<Team>_<id>_assignsubmission_file/<file>
    input/<run>/<Grades-....csv>

Two ways in, one code path after that:
  * "upload"   - the UI stored a zip (+ CSV) under input/<run>/_source/
  * "incoming" - the admin copied a batch with the AWS S3 CLI to incoming/<batch>/ : either the zip,
                 or the already unzipped Moodle folders (exactly what was done for cosmos), plus the CSV.
Everything is copied/unpacked into the canonical tree; sources are never modified. Idempotent: an
object that already exists with the same size is skipped, and a team whose manifest matches is not re-read.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path

from core import keys, moodle
from core.manifest import hash_obj, is_done, sha256_bytes, write_manifest
from core.storage import Storage, put_json

STAGE = "s1_ingest"
STAGE_VERSION = "2"


class IngestProgress:
    """Live counters for the ingest progress bar, published over SSE while stage 1 runs.

    ``add_bytes`` is called from boto3's transfer thread, so every mutation takes a lock.
    ``snapshot()`` returns flat values the web UI renders directly; ``percent`` weights the
    phases by their usual cost (the source download dominates, team registration is cheap).
    """

    WEIGHTS = {"bytes": 45.0, "unpack": 40.0, "register": 15.0}

    def __init__(self) -> None:
        self._lock = threading.RLock()   # reentrant: snapshot() reads the counters via percent()
        self._bytes_total = 0
        self._bytes_done = 0
        self._totals = {"unpack": 0, "register": 0}
        self._done = {"unpack": 0, "register": 0}

    def set_bytes_total(self, total: int) -> None:
        with self._lock:
            self._bytes_total = total

    def add_bytes(self, n: int) -> None:
        with self._lock:
            self._bytes_done += n

    def add_total(self, phase: str, n: int) -> None:
        with self._lock:
            self._totals[phase] += n

    def bump(self, phase: str, n: int = 1) -> None:
        with self._lock:
            self._done[phase] += n

    def percent(self) -> int | None:
        with self._lock:
            parts = []
            if self._bytes_total > 0:
                parts.append((self.WEIGHTS["bytes"], min(1.0, self._bytes_done / self._bytes_total)))
            for phase in ("unpack", "register"):
                if self._totals[phase] > 0:
                    parts.append((self.WEIGHTS[phase], min(1.0, self._done[phase] / self._totals[phase])))
            if not parts:
                return None
            return round(sum(w * f for w, f in parts) / sum(w for w, _ in parts) * 100)

    def snapshot(self) -> dict:
        with self._lock:
            return {"percent": self.percent(), "bytes_done": self._bytes_done, "bytes_total": self._bytes_total,
                    "unpack_done": self._done["unpack"], "unpack_total": self._totals["unpack"],
                    "register_done": self._done["register"], "register_total": self._totals["register"]}


@dataclass
class IngestedTeam:
    name: str
    slug: str
    submission_id: str
    rel_dir: str
    filename: str
    file_key: str
    file_sha256: str
    file_size: int
    is_pdf: bool
    extra_files: list[str]
    skipped_existing: bool = False


async def _materialize(data: Storage, run_id: int, source: dict, progress: IngestProgress | None = None) -> dict:
    """Copy/unpack the source into input/<run>/ . Returns {"copied", "skipped", "worksheet_key"}."""
    stats = {"copied": 0, "skipped": 0, "worksheet_key": None, "zips": 0}
    csvs: list[str] = []

    async def put_member(rel: str, size: int, writer) -> None:
        dst = keys.canonical(run_id, rel)
        if await data.size(dst) == size:
            stats["skipped"] += 1
        else:
            await writer(dst)
            stats["copied"] += 1
        if progress:
            progress.bump("unpack")

    if source.get("kind") == "incoming":
        prefixes = source.get("prefixes") or [source["prefix"]]
    else:  # legacy run-level upload
        prefixes = [keys.source_prefix(run_id)]
    work: list[tuple[str, str, int]] = []                      # (batch prefix, key, size), batches in the order they were added
    for prefix in prefixes:
        metas = await data.list_meta(prefix)
        if not metas:
            raise FileNotFoundError(f"nothing found under {prefix}")
        work += [(prefix, k, sz) for k, sz in metas]
    if progress:
        progress.set_bytes_total(sum(sz for _, k, sz in work if k.lower().endswith(".zip")))

    for prefix, key, size in work:
        rel = key[len(prefix):]
        low = rel.lower()
        if moodle.is_junk(rel):
            continue
        if low.endswith(".zip"):
            stats["zips"] += 1
            with tempfile.TemporaryDirectory() as tmp:
                zpath = Path(tmp) / "s.zip"
                if not await data.get_file(key, zpath, progress.add_bytes if progress else None):
                    raise FileNotFoundError(key)
                with zipfile.ZipFile(zpath) as zf:
                    members = [i for i in zf.infolist()
                               if not i.is_dir() and not moodle.is_junk(i.filename)
                               and (moodle.group_submissions([i.filename]) or i.filename.lower().endswith(".csv"))]
                    if progress:
                        progress.add_total("unpack", len(members))
                    for info in members:
                        name = info.filename
                        is_sub = bool(moodle.group_submissions([name]))
                        dst_rel = name if is_sub else name.rsplit("/", 1)[-1]
                        if dst_rel.lower().endswith(".csv"):
                            csvs.append(dst_rel)

                        async def w(dst, name=name):
                            await data.put(dst, zf.read(name))

                        await put_member(dst_rel, info.file_size, w)
        elif low.endswith(".csv"):
            dst_rel = rel.rsplit("/", 1)[-1]
            csvs.append(dst_rel)
            if progress:
                progress.add_total("unpack", 1)

            async def wc(dst, key=key):
                await data.copy(key, dst)

            await put_member(dst_rel, size, wc)
        elif moodle.group_submissions([rel]):
            if progress:
                progress.add_total("unpack", 1)

            async def wf(dst, key=key):
                await data.copy(key, dst)

            await put_member(rel, size, wf)
    pick = [c for c in csvs if c.startswith("Grades-")] or csvs       # csvs are in batch order: the newest batch wins
    stats["worksheet_key"] = keys.canonical(run_id, pick[-1]) if pick else None
    return stats


async def ingest(data: Storage, run_id: int, source: dict,
                 progress: IngestProgress | None = None) -> tuple[list[IngestedTeam], dict]:
    mat = await _materialize(data, run_id, source, progress)
    root = keys.input_prefix(run_id)
    metas = [(k[len(root):], s) for k, s in await data.list_meta(root) if not k[len(root):].startswith("_source/")]
    size_of = dict(metas)
    out: list[IngestedTeam] = []
    used: set[str] = set()
    subs = list(moodle.group_submissions(size_of))
    if progress:
        progress.add_total("register", len(subs))
    for sub in subs:
        pdfs = [f for f in sub.files if f.lower().endswith(".pdf")]
        chosen = sorted(pdfs or sub.files)[0]
        slug = keys.slugify(f"{sub.team}_{sub.submission_id}")
        while slug in used:
            slug += "_x"
        used.add(slug)
        fname = chosen.rsplit("/", 1)[-1]
        key = keys.canonical(run_id, chosen)
        inputs_hash = hash_obj({"rel": chosen, "size": size_of[chosen]})
        cfg_hash = hash_obj({"v": STAGE_VERSION})
        rec_path = keys.artifact(run_id, slug, "s1_upload.json")
        extra = [f.rsplit("/", 1)[-1] for f in sub.files if f != chosen]
        if await is_done(data, run_id, slug, STAGE, inputs_hash, cfg_hash):
            rec = json.loads(await data.get(rec_path))
            out.append(IngestedTeam(**rec, skipped_existing=True))
            if progress:
                progress.bump("register")
            continue
        blob = await data.get(key)
        rec = dict(name=sub.team, slug=slug, submission_id=sub.submission_id, rel_dir=sub.rel_dir, filename=fname,
                   file_key=key, file_sha256=sha256_bytes(blob), file_size=len(blob),
                   is_pdf=fname.lower().endswith(".pdf") and blob[:5] == b"%PDF-", extra_files=extra)
        await put_json(data, rec_path, rec)
        await write_manifest(data, run_id, slug, STAGE, inputs_hash, cfg_hash, [rec_path, key])
        out.append(IngestedTeam(**rec))
        if progress:
            progress.bump("register")
    report: dict = {"teams": len(out), "resumed": sum(t.skipped_existing for t in out),
                    "files_copied": mat["copied"], "files_skipped": mat["skipped"], "zips": mat["zips"],
                    "worksheet_key": mat["worksheet_key"]}
    if mat["worksheet_key"]:
        rows = moodle.parse_worksheet(await data.get(mat["worksheet_key"]))
        match = moodle.match_groups([t.name for t in out], rows)
        report.update(worksheet_rows=len(rows), unmatched_teams=[n for n, c in match.items() if c == 0], _match=match)
    return out, report
