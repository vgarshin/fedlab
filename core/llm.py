"""LLM gateway: one async interface for Yandex AI Studio and Cloud.ru (both OpenAI-compatible).

Cross-cutting concerns live here and nowhere else:
  * concurrency limit per model (asyncio.Semaphore from the registry),
  * response cache in S3 keyed by (model, params, system, user) => re-runs are free,
  * every real call is recorded (tokens, cost, latency) in ``llm_calls``,
  * a hard per-run budget cap checked BEFORE each paid call.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass

from openai import AsyncOpenAI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.config import Secrets, Settings
from core.db import LlmCall, ModelEntry
from core.storage import Storage


class LLMError(RuntimeError):
    pass


class BudgetExceeded(LLMError):
    """Not retryable: the run's spending cap was reached."""


@dataclass
class LlmResult:
    text: str
    prompt_tokens: int
    completion_tokens: int
    cost_rub: float
    latency_ms: int
    cache_hit: bool
    cached_tokens: int = 0
    original_latency_ms: int = 0       # for cache hits: how long the real call took when it was made


@dataclass
class CallTag:
    """Who is asking; stored with the call for the cost dashboard."""
    run_id: int | None
    team_id: int | None
    stage: str
    variant: str = ""
    prompt_name: str = ""
    prompt_version: int = 0


def cost_rub(m: ModelEntry, pt: int, ct: int, cached: int = 0) -> float:
    """Cached prompt tokens (a repeated static prefix, e.g. the long grading prompt) are billed at the cached price."""
    cached = min(cached, pt)
    cached_price = m.price_cached_per_1m or m.price_in_per_1m
    return round((pt - cached) * m.price_in_per_1m / 1e6 + cached * cached_price / 1e6 + ct * m.price_out_per_1m / 1e6, 6)


class Gateway:
    def __init__(self, settings: Settings, secrets: Secrets, sessions: async_sessionmaker[AsyncSession],
                 cache: Storage, clients: dict[str, AsyncOpenAI] | None = None):
        self._settings, self._secrets, self._sessions, self._cache = settings, secrets, sessions, cache
        self._clients: dict[str, AsyncOpenAI] = clients or {}
        self._sems: dict[str, asyncio.Semaphore] = {}

    def _client(self, provider: str) -> AsyncOpenAI:
        if provider not in self._clients:      # created lazily: unused providers need no key
            if provider == "yandex":
                cfg = self._settings.yandex
                if not cfg.folder_id:
                    raise LLMError("settings.yandex.folder_id is empty")
                self._clients[provider] = AsyncOpenAI(
                    api_key=self._secrets.require("yandex_llm", "api_key"), base_url=cfg.llm_base_url,
                    project=cfg.folder_id, timeout=cfg.llm_timeout_seconds, max_retries=2)
            elif provider == "cloudru":
                cfg = self._settings.cloudru
                if not cfg.llm_base_url:
                    raise LLMError("settings.cloudru.llm_base_url is empty (Cloud.ru is not configured yet)")
                self._clients[provider] = AsyncOpenAI(
                    api_key=self._secrets.require("cloudru_llm", "api_key"), base_url=cfg.llm_base_url,
                    timeout=cfg.llm_timeout_seconds, max_retries=2)
            else:
                raise LLMError(f"unknown provider {provider}")
        return self._clients[provider]

    def _model_name(self, m: ModelEntry) -> str:
        return f"gpt://{self._settings.yandex.folder_id}/{m.model}" if m.provider == "yandex" else m.model

    @staticmethod
    def cache_key(m: ModelEntry, system: str, user: str, temperature: float) -> str:
        raw = json.dumps([m.provider, m.model, m.reasoning_effort, temperature, system, user], ensure_ascii=False)
        return f"_cache/llm/{m.id}/{hashlib.sha256(raw.encode()).hexdigest()}.json"

    async def spent(self, run_id: int) -> float:
        async with self._sessions() as s:
            return float((await s.execute(select(func.coalesce(func.sum(LlmCall.cost_rub), 0.0))
                                          .where(LlmCall.run_id == run_id))).scalar_one())

    async def complete(self, m: ModelEntry, system: str, user: str, tag: CallTag, *, cap_rub: float | None = None,
                       temperature: float = 0.0, json_mode: bool = True, use_cache: bool = True) -> LlmResult:
        key = self.cache_key(m, system, user, temperature)
        cached = await self._cache.get(key) if use_cache else None
        if cached is not None:
            rec = json.loads(cached)
            await self._record(m, tag, rec["prompt_tokens"], rec["completion_tokens"], 0.0, 0, True, cached=0)
            return LlmResult(rec["text"], rec["prompt_tokens"], rec["completion_tokens"], 0.0, 0, True,
                             original_latency_ms=int(rec.get("latency_ms", 0)))
        if cap_rub is not None and tag.run_id is not None and await self.spent(tag.run_id) >= cap_rub:
            raise BudgetExceeded(f"Run budget cap {cap_rub:.2f} RUB reached")

        extra: dict = {}
        if m.reasoning_effort:
            extra["reasoning_effort"] = m.reasoning_effort
        if json_mode:
            extra["response_format"] = {"type": "json_object"}
        sem = self._sems.setdefault(m.id, asyncio.Semaphore(max(1, m.max_concurrency)))
        started = time.perf_counter()
        async with sem:
            try:
                resp = await self._client(m.provider).chat.completions.create(
                    model=self._model_name(m), temperature=temperature,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}], **extra)
            except Exception as exc:
                await self._record(m, tag, 0, 0, 0.0, int((time.perf_counter() - started) * 1000), False,
                                   "error", f"{type(exc).__name__}: {str(exc)[:300]}")
                raise LLMError(f"{type(exc).__name__}: {str(exc)[:300]}") from exc
        ms = int((time.perf_counter() - started) * 1000)
        pt = resp.usage.prompt_tokens if resp.usage else 0
        ct = resp.usage.completion_tokens if resp.usage else 0
        text = resp.choices[0].message.content or ""
        details = getattr(resp.usage, "prompt_tokens_details", None) if resp.usage else None
        cached = int(getattr(details, "cached_tokens", 0) or 0)
        c = cost_rub(m, pt, ct, cached)
        await self._cache.put(key, json.dumps({"text": text, "prompt_tokens": pt, "completion_tokens": ct, "latency_ms": ms},
                                              ensure_ascii=False).encode(), "application/json")
        await self._record(m, tag, pt, ct, c, ms, False, cached=cached)
        return LlmResult(text, pt, ct, c, ms, False, cached, ms)

    async def _record(self, m: ModelEntry, tag: CallTag, pt: int, ct: int, cost: float, ms: int, hit: bool,
                      status: str = "ok", error: str | None = None, cached: int = 0) -> None:
        async with self._sessions() as s:
            s.add(LlmCall(run_id=tag.run_id, team_id=tag.team_id, stage=tag.stage, variant=tag.variant,
                          model_id=m.id, prompt_name=tag.prompt_name, prompt_version=tag.prompt_version,
                          prompt_tokens=pt, completion_tokens=ct, cached_tokens=cached, cost_rub=cost, latency_ms=ms,
                          cache_hit=hit, status=status, error=error))
            await s.commit()
