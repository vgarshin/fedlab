"""Connectivity check for both buckets: write, read back, list, delete a probe object.

    docker compose run --rm --no-deps api python -m core.s3check
Prints only OK/FAIL per step, never keys.
"""
from __future__ import annotations

import asyncio
import sys
import uuid

from core.config import get_secrets, get_settings
from core.stores import make_stores


async def main() -> int:
    stores = make_stores(get_settings(), get_secrets())
    probe = f"_healthcheck/{uuid.uuid4().hex}.txt"
    ok = True
    for label, st in (("input", stores.data), ("result", stores.result)):
        try:
            await st.put(probe, b"fedlab probe")
            assert await st.get(probe) == b"fedlab probe", "read-back mismatch"
            assert probe in await st.list("_healthcheck/"), "not listed"
            await st.delete(probe)
            assert not await st.exists(probe), "still exists after delete"
            print(f"{label:7} bucket {st.bucket}: OK (put/get/list/delete)")
        except Exception as exc:  # report and continue with the other bucket
            ok = False
            print(f"{label:7} bucket {st.bucket}: FAIL - {type(exc).__name__}: {str(exc)[:200]}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
