"""Optional Yandex OCR (opt-in per run). Page-level cache in S3 => resumable and never paid twice.

Used only for PDFs without a usable text layer. Rate-limited to the Yandex quota
(1 request/s). Not used for QR codes: those are found on page images with OpenCV.
"""
from __future__ import annotations

import asyncio
import base64
import time

import httpx

from core import keys
from core.config import OcrCfg
from core.storage import Storage, get_json, put_json

RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 6


class OCRError(RuntimeError):
    pass


class RateLimiter:
    def __init__(self, per_second: float):
        self._interval = 1.0 / per_second if per_second > 0 else 0.0
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def wait(self) -> None:
        if not self._interval:
            return
        async with self._lock:
            now = time.monotonic()
            slot = max(now, self._next)
            self._next = slot + self._interval
        if slot > now:
            await asyncio.sleep(slot - now)


class YandexOCR:
    def __init__(self, cfg: OcrCfg, folder_id: str, api_key: str, storage: Storage,
                 client: httpx.AsyncClient | None = None):
        self._cfg, self._st = cfg, storage
        self._limiter = RateLimiter(cfg.max_requests_per_second)
        self._client = client or httpx.AsyncClient(timeout=cfg.timeout_seconds)
        self._headers = {"Authorization": f"Api-Key {api_key}", "x-folder-id": folder_id,
                         "x-data-logging-enabled": "false"}

    async def page_text(self, file_sha256: str, number: int, png: bytes) -> tuple[str, bool]:
        """Returns (text, cache_hit). Result is persisted before returning."""
        key = keys.ocr_page(file_sha256, self._cfg.dpi, number)
        cached = await get_json(self._st, key)
        if cached is not None:
            return cached["text"], True
        body = {"mimeType": "image/png", "languageCodes": self._cfg.languages, "model": self._cfg.model,
                "content": base64.b64encode(png).decode()}
        payload = await self._post(body)
        ann = payload.get("result", payload).get("textAnnotation")
        if not isinstance(ann, dict):
            raise OCRError("OCR response has no textAnnotation")
        text = str(ann.get("fullText", ""))
        await put_json(self._st, key, {"text": text, "model": self._cfg.model, "dpi": self._cfg.dpi})
        return text, False

    async def _post(self, body: dict) -> dict:
        url = f"{self._cfg.base_url}/recognizeText"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            await self._limiter.wait()
            try:
                r = await self._client.post(url, json=body, headers=self._headers)
            except httpx.TransportError as exc:
                if attempt == MAX_ATTEMPTS:
                    raise OCRError(f"OCR connection failed: {type(exc).__name__}") from None
            else:
                if r.status_code == 200:
                    return r.json()
                if r.status_code not in RETRY_STATUSES or attempt == MAX_ATTEMPTS:
                    raise OCRError(f"OCR returned {r.status_code}: {r.text[:200]}")
            await asyncio.sleep(min(2 ** (attempt - 1), 8))
        raise AssertionError("unreachable")
