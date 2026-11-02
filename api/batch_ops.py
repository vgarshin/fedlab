"""Batch operations shared by the batches and runs routers."""
from __future__ import annotations

import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException, UploadFile

from api.deps import AppState
from core import batches, moodle
from core.db import Batch, Run, User


def prefix_of(state: AppState, name: str) -> str:
    return f"{state.settings.incoming_prefix}{name}/"


def check_name(name: str) -> str:
    name = name.strip()
    if not batches.NAME_RE.match(name):
        raise HTTPException(422, f"Invalid batch name “{name}”: {batches.NAME_HELP}")
    return name


async def name_taken(state: AppState, db, name: str) -> bool:
    return await db.get(Batch, name) is not None or bool(await state.stores.data.list_meta(prefix_of(state, name)))


async def unique_default(state: AppState, db) -> str:
    base = batches.default_name(datetime.now(timezone.utc))
    name, n = base, 1
    while await name_taken(state, db, name):
        n += 1
        name = f"{base}-{n}"
    return name


async def runs_using(db, name: str) -> list[int]:
    from sqlalchemy import select
    out = []
    for r in (await db.execute(select(Run))).scalars():
        src = r.source or {}
        names = src.get("batches") or ([src["prefix"].rstrip("/").rsplit("/", 1)[-1]] if src.get("prefix") else [])
        if name in names:
            out.append(r.id)
    return sorted(out)


async def describe(state: AppState, db, name: str, row: Batch | None, metas: list[tuple[str, int]] | None = None) -> dict:
    sc = await batches.scan(state.stores.data, prefix_of(state, name), metas)
    modified = bool(row and row.status == "used" and row.sealed_hash != sc.listing_hash)
    problems = list(sc.problems)
    if modified:
        problems.append("Files were added, removed or replaced after a run used this batch. Create a new batch for new files.")
    status = "unregistered" if row is None else ("uploaded" if row.status == "reserved" and sc.files else row.status)
    return {"name": name, "status": status, "origin": row.origin if row else "outside", "registered": row is not None,
            "created_at": row.created_at if row else None, "files": sc.files, "bytes": sc.bytes, "zips": sc.zips, "csvs": sc.csvs,
            "unzipped_submissions": sc.unzipped_submissions, "problems": problems, "modified_after_use": modified,
            "usable": not problems, "used_by": await runs_using(db, name), "listing_hash": sc.listing_hash}


async def store_upload(state: AppState, db, user: User, name: str, submissions: UploadFile, worksheet: UploadFile | None) -> Batch:
    """Create batch `name` and fill it from the browser. Refuses an existing name (a batch is written once)."""
    name = check_name(name)
    if await name_taken(state, db, name):
        raise HTTPException(409, f"Batch “{name}” already exists. Files are never added to an existing batch: choose another name.")
    limit = state.settings.processing.max_upload_mb << 20
    zname = (submissions.filename or "submissions.zip").rsplit("/", 1)[-1]
    if not zname.lower().endswith(".zip"):
        raise HTTPException(400, "The submissions file must be a .zip archive")
    with tempfile.TemporaryDirectory() as tmp:
        zpath = Path(tmp) / "s.zip"
        size = 0
        with zpath.open("wb") as f:
            while chunk := await submissions.read(1 << 20):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"Zip is larger than {state.settings.processing.max_upload_mb} MB")
                f.write(chunk)
        if not zipfile.is_zipfile(zpath):
            raise HTTPException(400, "submissions must be a zip archive")
        with zipfile.ZipFile(zpath) as zf:
            if not moodle.group_submissions(i.filename for i in zf.infolist() if not i.is_dir()):
                raise HTTPException(400, "No Moodle submission folders (<Team>_<id>_assignsubmission_file) found in this zip")
        row = Batch(name=name, origin="ui", status="reserved", created_by=user.id)
        db.add(row)
        await db.flush()
        prefix = prefix_of(state, name)
        try:
            await state.stores.data.put_file(f"{prefix}{zname}", zpath)
            if worksheet is not None and worksheet.filename:
                cname = worksheet.filename.rsplit("/", 1)[-1]
                if not cname.lower().endswith(".csv"):
                    raise HTTPException(400, "The worksheet must be a .csv file")
                await state.stores.data.put(f"{prefix}{cname}", await worksheet.read(), "text/csv")
        except Exception:
            for k, _ in await state.stores.data.list_meta(prefix):
                await state.stores.data.delete(k)
            await db.rollback()
            raise
        row.status = "uploaded"
    return row


async def use_batch(state: AppState, db, run: Run, name: str, user: User) -> dict:
    """Attach a batch to a run. Seals it on first use; refuses invalid or modified batches."""
    name = check_name(name)
    row = await db.get(Batch, name)
    sc = await batches.scan(state.stores.data, prefix_of(state, name))
    if sc.files == 0 and row is None:
        raise HTTPException(404, f"No batch “{name}”")
    d = await describe(state, db, name, row)
    if d["problems"]:
        raise HTTPException(409, "This batch cannot be used: " + " ".join(d["problems"]))
    if row is None:
        row = Batch(name=name, origin="outside", status="uploaded", created_by=user.id)
        db.add(row)
    if row.status != "used":
        row.status, row.sealed_hash, row.sealed_at = "used", sc.listing_hash, datetime.now(timezone.utc)
    src = dict(run.source or {})
    names = list(src.get("batches") or ([src["prefix"].rstrip("/").rsplit("/", 1)[-1]] if src.get("prefix") else []))
    if name not in names:
        names.append(name)
    run.source = {"kind": "incoming", "batches": names, "prefixes": [prefix_of(state, n) for n in names],
                  "prefix": prefix_of(state, names[-1]), "worksheet_key": src.get("worksheet_key")}
    run.status = "uploaded"
    return run.source
