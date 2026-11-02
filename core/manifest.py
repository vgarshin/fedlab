"""Manifest = the "this unit of work is complete" marker.

Rule: a stage writes its artifacts first and the manifest LAST. A unit is done
only if the manifest exists AND its ``inputs_hash``/``config_hash`` equal the
current ones. A crash between artifacts and manifest leaves the unit "not
done", so it is recomputed (artifacts are overwritten with identical keys).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from core import keys
from core.storage import Storage, get_json, put_json


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_obj(obj: Any) -> str:
    """Stable hash of a JSON-serialisable object (key order does not matter)."""
    return sha256_bytes(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode())[:32]


async def is_done(st: Storage, run_id: int, slug: str, stage: str, inputs_hash: str, config_hash: str) -> bool:
    m = await get_json(st, keys.manifest(run_id, slug, stage))
    return bool(m) and m.get("status") == "done" and m.get("inputs_hash") == inputs_hash \
        and m.get("config_hash") == config_hash


async def write_manifest(
    st: Storage, run_id: int, slug: str, stage: str, inputs_hash: str, config_hash: str,
    outputs: list[str], extra: dict | None = None,
) -> dict:
    m = {
        "stage": stage, "status": "done", "inputs_hash": inputs_hash, "config_hash": config_hash,
        "outputs": outputs, "finished_at": datetime.now(timezone.utc).isoformat(), **(extra or {}),
    }
    await put_json(st, keys.manifest(run_id, slug, stage), m)
    return m
