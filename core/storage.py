"""Object storage abstraction: S3 (T1 Cloud) and local directory (tests/dev).

All methods are async; blocking boto3 calls run in a thread so the event loop
stays free. Writes are whole-object PUTs, which S3 makes atomic: a reader sees
either the old or the new object, never half of one. Idempotency relies on that.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Callable, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


class Storage(Protocol):
    bucket: str

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...
    async def get(self, key: str) -> bytes | None: ...
    async def exists(self, key: str) -> bool: ...
    async def list(self, prefix: str) -> list[str]: ...
    async def delete(self, key: str) -> None: ...
    async def size(self, key: str) -> int | None: ...
    async def list_meta(self, prefix: str) -> list[tuple[str, int]]: ...
    async def copy(self, src: str, dst: str) -> None: ...
    async def put_file(self, key: str, path: Path) -> None: ...
    async def get_file(self, key: str, path: Path, progress: Callable[[int], None] | None = None) -> bool: ...


async def put_json(st: Storage, key: str, obj: Any) -> None:
    await st.put(key, json.dumps(obj, ensure_ascii=False, indent=2).encode(), "application/json")


async def get_json(st: Storage, key: str) -> Any | None:
    raw = await st.get(key)
    return None if raw is None else json.loads(raw)


class S3Storage:
    def __init__(self, bucket: str, endpoint: str, region: str, key_id: str, secret: str):
        self.bucket = bucket
        self._c = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region or None,
            aws_access_key_id=key_id,
            aws_secret_access_key=secret,
            config=Config(retries={"max_attempts": 5, "mode": "standard"}, signature_version="s3v4"),
        )

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        await asyncio.to_thread(
            self._c.put_object, Bucket=self.bucket, Key=key, Body=data, ContentType=content_type
        )

    async def get(self, key: str) -> bytes | None:
        def _get() -> bytes | None:
            try:
                return self._c.get_object(Bucket=self.bucket, Key=key)["Body"].read()
            except ClientError as exc:
                if exc.response["Error"]["Code"] in ("NoSuchKey", "404"):
                    return None
                raise

        return await asyncio.to_thread(_get)

    async def exists(self, key: str) -> bool:
        def _head() -> bool:
            try:
                self._c.head_object(Bucket=self.bucket, Key=key)
                return True
            except ClientError as exc:
                if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                    return False
                raise

        return await asyncio.to_thread(_head)

    async def list(self, prefix: str) -> list[str]:
        def _list() -> list[str]:
            keys: list[str] = []
            for page in self._c.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
                keys += [o["Key"] for o in page.get("Contents", [])]
            return keys

        return await asyncio.to_thread(_list)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._c.delete_object, Bucket=self.bucket, Key=key)

    async def size(self, key: str) -> int | None:
        def _size() -> int | None:
            try:
                return int(self._c.head_object(Bucket=self.bucket, Key=key)["ContentLength"])
            except ClientError as exc:
                if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                    return None
                raise

        return await asyncio.to_thread(_size)

    async def list_meta(self, prefix: str) -> list[tuple[str, int]]:
        def _list() -> list[tuple[str, int]]:
            out: list[tuple[str, int]] = []
            for page in self._c.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
                out += [(o["Key"], int(o["Size"])) for o in page.get("Contents", [])]
            return out

        return await asyncio.to_thread(_list)

    async def copy(self, src: str, dst: str) -> None:
        """Server-side copy inside the bucket: no data passes through our VM."""
        await asyncio.to_thread(self._c.copy, {"Bucket": self.bucket, "Key": src}, self.bucket, dst)

    async def put_file(self, key: str, path: Path) -> None:
        await asyncio.to_thread(self._c.upload_file, str(path), self.bucket, key)

    async def get_file(self, key: str, path: Path, progress: Callable[[int], None] | None = None) -> bool:
        """Download to a local file. ``progress`` gets the number of bytes moved per chunk
        (called from boto3's transfer thread; keep the receiver thread-safe)."""
        def _dl() -> bool:
            try:
                self._c.download_file(self.bucket, key, str(path), Callback=progress)
                return True
            except ClientError as exc:
                if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                    return False
                raise

        return await asyncio.to_thread(_dl)


class LocalStorage:
    """Directory-backed storage with the same semantics (atomic writes via rename)."""

    def __init__(self, root: Path, bucket: str = "local"):
        self.bucket = bucket
        self._root = Path(root) / bucket
        self._root.mkdir(parents=True, exist_ok=True)

    def _p(self, key: str) -> Path:
        p = (self._root / key).resolve()
        if self._root.resolve() not in p.parents and p != self._root.resolve():
            raise ValueError(f"key escapes storage root: {key}")
        return p

    async def put(self, key: str, data: bytes, content_type: str = "") -> None:
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)

    async def get(self, key: str) -> bytes | None:
        p = self._p(key)
        return p.read_bytes() if p.is_file() else None

    async def exists(self, key: str) -> bool:
        return self._p(key).is_file()

    async def list(self, prefix: str) -> list[str]:
        base = self._root.resolve()
        return sorted(
            str(p.relative_to(base)) for p in base.rglob("*")
            if p.is_file() and not p.name.endswith(".tmp") and str(p.relative_to(base)).startswith(prefix)
        )

    async def delete(self, key: str) -> None:
        self._p(key).unlink(missing_ok=True)

    async def size(self, key: str) -> int | None:
        p = self._p(key)
        return p.stat().st_size if p.is_file() else None

    async def list_meta(self, prefix: str) -> list[tuple[str, int]]:
        return [(k, (await self.size(k)) or 0) for k in await self.list(prefix)]

    async def copy(self, src: str, dst: str) -> None:
        data = await self.get(src)
        if data is None:
            raise FileNotFoundError(src)
        await self.put(dst, data)

    async def put_file(self, key: str, path: Path) -> None:
        await self.put(key, Path(path).read_bytes())

    async def get_file(self, key: str, path: Path, progress: Callable[[int], None] | None = None) -> bool:
        data = await self.get(key)
        if data is None:
            return False
        Path(path).write_bytes(data)
        if progress:
            progress(len(data))
        return True
