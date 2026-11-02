"""Upload batches (incoming/<name>/): reserve a name, upload from the browser, list with validity, delete unused ones."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select

from api import batch_ops as ops
from api.deps import Admin, Db, State
from core.db import AuditLog, Batch

router = APIRouter(prefix="/api/batches", tags=["batches"])


@router.get("")
async def list_batches(state: State, _: Admin, db: Db) -> dict:
    """Every batch: registered ones plus folders found in the bucket that were never reserved. Includes the validity verdict."""
    pref = state.settings.incoming_prefix
    grouped: dict[str, list[tuple[str, int]]] = {}
    for key, size in await state.stores.data.list_meta(pref):
        rel = key[len(pref):]
        if "/" in rel:
            grouped.setdefault(rel.split("/", 1)[0], []).append((key, size))
    rows = {b.name: b for b in (await db.execute(select(Batch))).scalars()}
    out = []
    for n in sorted(set(grouped) | set(rows)):
        d = await ops.describe(state, db, n, rows.get(n), grouped.get(n, []))
        if n not in rows and d["used_by"]:
            # A run already uses this folder although it was never reserved (runs made before batches existed, or via the admin tool):
            # adopt it as a "used" batch and seal its current content, so later changes are detected from now on.
            db.add(Batch(name=n, origin="outside", status="used", sealed_hash=d["listing_hash"], sealed_at=datetime.now(timezone.utc)))
            await db.commit()
            d.update(status="used", registered=True)
        out.append(d)
    return {"batches": sorted(out, key=lambda b: (b["created_at"] is None, str(b["created_at"] or ""), b["name"]), reverse=True),
            "name_rule": "3-60 characters: letters, digits, dot, dash, underscore; start with a letter or digit"}


class Reserve(BaseModel):
    name: str | None = None


@router.post("", status_code=201)
async def reserve(body: Reserve, state: State, user: Admin, db: Db) -> dict:
    """Reserve a NEW batch name for an upload with the AWS CLI. An existing name is refused."""
    name = ops.check_name(body.name) if body.name else await ops.unique_default(state, db)
    if await ops.name_taken(state, db, name):
        raise HTTPException(409, f"Batch “{name}” already exists. Files are never added to an existing batch: choose another name.")
    db.add(Batch(name=name, origin="cli", status="reserved", created_by=user.id))
    db.add(AuditLog(user_id=user.id, action="batch_reserve", details={"name": name}))
    await db.commit()
    cfg = state.settings.object_storage
    dest = f"s3://{cfg.buckets.input}/{state.settings.incoming_prefix}{name}/"
    flags = f"--endpoint-url {cfg.endpoint} --region {cfg.region or 'dc1'}"
    return {"name": name, "prefix": ops.prefix_of(state, name),
            "commands": [f"aws s3 cp ./moodle_export.zip {dest} {flags}", f"aws s3 cp ./Grades-worksheet.csv {dest} {flags}"],
            "commands_unzipped": f"aws s3 cp --recursive ./moodle_export/ {dest} {flags}"}


@router.post("/upload", status_code=201)
async def upload(state: State, user: Admin, db: Db, name: str = Form(...), submissions: UploadFile = File(...),
                 worksheet: UploadFile | None = File(None)) -> dict:
    """Create a new batch from the browser (zip + optional worksheet CSV) in one step."""
    row = await ops.store_upload(state, db, user, name, submissions, worksheet)
    db.add(AuditLog(user_id=user.id, action="batch_upload", details={"name": row.name}))
    await db.commit()
    return {"name": row.name}


@router.delete("/{name}")
async def delete_batch(name: str, state: State, user: Admin, db: Db) -> dict:
    """Delete a batch that no run has used (for example a mistaken one). Used batches are kept: runs refer to them."""
    name = ops.check_name(name)
    used = await ops.runs_using(db, name)
    row = await db.get(Batch, name)
    if used or (row and row.status == "used"):
        raise HTTPException(409, f"Batch “{name}” was used by run(s) {used or '?'} and is kept for traceability.")
    n = 0
    for key, _ in await state.stores.data.list_meta(ops.prefix_of(state, name)):
        await state.stores.data.delete(key)
        n += 1
    if row:
        await db.delete(row)
    db.add(AuditLog(user_id=user.id, action="batch_delete", details={"name": name, "objects": n}))
    await db.commit()
    return {"deleted_objects": n}
