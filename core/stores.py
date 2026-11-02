"""Factory for the two buckets used by the app."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from core.config import Settings, Secrets
from core.storage import LocalStorage, S3Storage, Storage


@dataclass(frozen=True)
class Stores:
    data: Storage    # input + _work + _cache  (fedlab-grade-data)
    result: Storage  # stage-5 exports         (fedlab-result-data)


def make_stores(settings: Settings, secrets: Secrets) -> Stores:
    cfg = settings.object_storage
    if settings.storage.get("backend") == "local":
        root = Path(os.environ.get("FEDLAB_LOCAL_STORAGE", "/data/local-storage"))
        return Stores(LocalStorage(root, cfg.buckets.input), LocalStorage(root, cfg.buckets.result))
    key_id = secrets.require("object_storage", "access_key_id")
    secret = secrets.require("object_storage", "secret_access_key")
    return Stores(
        S3Storage(cfg.buckets.input, cfg.endpoint, cfg.region, key_id, secret),
        S3Storage(cfg.buckets.result, cfg.endpoint, cfg.region, key_id, secret),
    )
