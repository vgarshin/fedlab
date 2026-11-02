"""Live progress: workers publish small JSON events to Redis; the API relays them as SSE."""
from __future__ import annotations

import json


def channel(run_id: int) -> str:
    return f"fedlab:run:{run_id}"


async def publish(redis, run_id: int, **event) -> None:
    if redis is None:
        return
    try:
        await redis.publish(channel(run_id), json.dumps(event, default=str))
    except Exception:  # progress is best-effort; never fail a job because of it
        pass
