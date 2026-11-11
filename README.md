# fedlab — AI grading system for the Federal Business Solutions Lab

LLM-based grading of student **team** works (PDF slide decks) for the ВТБ
«Лаборатория бизнес-решений» course, autumn 2026. Moodle export in (zip +
grading worksheet) → text extraction → LLM grading (6 criteria, JSON schema,
score arithmetic done by code) → results back to Moodle.

Deployed at **https://aigrade.tech** (VM `fedlab-01`, T1 Cloud S3 buckets
`fedlab-grade-data` / `fedlab-result-data`). Auth: Yandex ID OAuth, roles
`admin` / `viewer`, plus API keys for integrations.

## Pipeline

1. **Ingest** — a write-once Moodle batch (`incoming/<batch>/`) is copied into
   `input/<run_id>/` in the cosmos layout; teams and worksheet matches are
   registered. UI zip upload, AWS-CLI zip or unzipped folders all work.
2. **Extract** — PyMuPDF per-slide text + technical metrics (chars with spaces,
   fonts, links, QR codes, external references, empty slides). Technical
   pre-check (not a PDF / anonymous / not machine-readable) → 0 points and **no
   LLM call**. OCR is opt-in per run and page-level (only slides without a text
   layer; cached per page).
3. **AI detection** — informational only, never changes scores; can be off.
4. **Grading** — LLM returns schema-constrained JSON (6 criteria × 1–10,
   feedback, evidence); Python recomputes `sum_score_60` / `total_score_100`,
   validates the contract, runs a forbidden-words repair retry. Injection and
   manipulation hints are scanned in code and hidden from the grader's input.
5. **Export** — Moodle worksheet CSV for re-import, per-team feedback files,
   aggregate JSONs, `jury_report.csv`, `run_report.json`. Re-runnable at any
   time.

## Architecture

```
nginx (80/443, static React build) ──► FastAPI (api/) ──► PostgreSQL (state, prompts, models, usage)
                                            │    └──► Redis (arq queue + pub/sub → SSE live progress)
                                            └──► arq worker(s) ──► T1 Cloud S3 (artifacts, caches)
                                                                  ──► LLMs: Yandex AI Studio, Cloud.ru
                                                                  ──► Yandex OCR (optional)
```

Docker Compose services: `nginx`, `api`, `worker`, `web` (build stage), `postgres`,
`redis`; prod overrides add certbot/TLS. Secrets via compose secrets
(`config/secrets.json`, never in git or images).

Key invariants: every stage unit writes artifacts first and `manifest.json`
LAST (`core/manifest.py`) — a unit is done only if manifest + input/config
hashes match, so resume skips done work. LLM answers and OCR pages are cached
in `_cache/` (exact key → repeat requests are free); rejected answers are
evicted from the cache so retries re-ask the model.

## Layout

| Path | What |
|---|---|
| `core/` | Pipeline (no web deps): config, storage, LLM gateway, PDF extract, OCR, injection scan, stages, export, runs, batches, admin tools |
| `api/` | FastAPI: auth (Yandex ID + API keys), RBAC, routes, SSE |
| `worker/` | arq jobs: ingest, stages, run-all, export, run deletion |
| `web/` | React 18 + TypeScript + Vite UI (see `web/README.md` — a first-React guide) |
| `prompts/` | Versioned LLM prompts, exported from the prompt store |
| `alembic/` | Migrations 0001–0005 (api and worker migrate at start under a lock) |
| `tests/` | Automated tests (run in Docker, see commands) |
| `deploy/` | Server bootstrap, deploy script, nginx/certbot, secrets prep |
| `docs/` | `STORAGE.md` (buckets, batches), `RESULTS.md` (export files), `API.md` (integrations), `PROJECT_ANALYSIS.md` (deep static review), `INJECTIONS.md` + `_RU` (manipulation hardening), `PARTIALLY_READABLE_RU.md` |
| `plans/` | Source requirements plan (`03-federal-lab-autumn-2026-plan.md`) |
| `scripts/` | E2E dev script, Playwright screenshot tooling |

## Commands (everything runs in Docker; the host has no Node/uv)

- Tests: `docker compose -f docker-compose.test.yml run --rm test`
- Dev stack: `deploy/prepare-secrets.sh && docker compose up --build`
  → UI http://localhost:5173, API http://localhost:8100/api/docs
- S3 check: `docker compose run --rm --no-deps api python -m core.s3check`
- Web build/typecheck: see `web/README.md`
- Deploy to VM: `SSH_CONFIG=/home/vgarshin/yadisk/clouds/config deploy/deploy.sh fedlab-01`

## Rules for agents

See `AGENTS.md` in this folder. Short version: artifacts stay inside `fedlab/`;
**never read or print `config/secrets.json`** (verify access by running
`python -m core.s3check`); score arithmetic is done by code, never trusted from
the LLM; code/comments/UI in English, LLM prompts in Russian.

## History

The project plan and decision log (milestone statuses from 18.10 to 03.11,
owner decisions, model comparisons, hardening notes) lived in `PLAN.md`; the
plan was completed and the file removed on 2026-11-11. Recover it from git:
`git log --follow -- PLAN.md`. Open roadmap items intentionally left for later:
stage-2b anonymization, score-stability repeats, similarity check between
works, over-limit text trimming.
