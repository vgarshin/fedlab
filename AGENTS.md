# AGENTS.md — fedlab

AI grading of student team works (PDF decks from Moodle) with LLMs. Project overview and commands: `README.md`. Decisions and milestone history: the former `PLAN.md` (closed and deleted 2026-11-11; recover with `git log --follow -- PLAN.md`; the source-requirements plan stays in `plans/03-federal-lab-autumn-2026-plan.md`). Language of code, comments, UI and docs: English; LLM prompts: Russian.

## Rules
- Store project artifacts only inside `fedlab/`. Reference projects (`../cosmos`, `../../metadocs`) are read-only samples.
- **Never read or print `config/secrets.json`**; never ask for keys in chat. Verify access by running code (`python -m core.s3check`).
- Score arithmetic (sum, total_score_100) is done by code, never trusted from the LLM.
- Idempotency: a stage writes artifacts first, `manifest.json` LAST (`core/manifest.py`); a unit is done only if the manifest matches input and config hashes.
- The LLM response cache never replays a rejected answer: rejections are evicted (`Gateway.evict`) and repair attempts bypass the cache (`use_cache=False`), so a retry or a re-run re-asks the model instead of looping on the same failure.
- Technical pre-check FAIL (not PDF / anonymous / not machine-readable) => 0 points and NO LLM call. OCR is opt-in per run.
- AI detection (stage 3) is informational and never changes scores.
- Manipulation hints (stage 2 `HIDDEN_TEXT`/`INJECTION_SUSPECTED` -> `technical_warnings`; stage 3 `manipulation_attempt` -> `quality_flags`) and `PARTIALLY_READABLE` (stage 2: some slides have no usable text layer, §32 in PLAN.md) never change scores, are hidden from the grader's input and do not use `needs_human_review`. See `docs/INJECTIONS.md`.
- Async everywhere on I/O; CPU-bound PDF work goes through `asyncio.to_thread`.

## Layout
`core/` pipeline (no web deps) · `api/` FastAPI · `worker/` arq jobs · `web/` React (Vite+TS) · `prompts/` · `deploy/` · `tests/`.

## Commands (everything runs in Docker; the host has no Node/uv)
- Tests: `docker compose -f docker-compose.test.yml run --rm test`
- Dev stack: `deploy/prepare-secrets.sh && docker compose up --build` → UI http://localhost:5173, API http://localhost:8100/api/docs
- S3 check: `docker compose run --rm --no-deps api python -m core.s3check`
- Web build/typecheck: see `web/README.md`
- Deploy to VM: `SSH_CONFIG=/home/vgarshin/yadisk/clouds/config deploy/deploy.sh fedlab-01`
