# fedlab — AI grading system for the Federal Business Solutions Lab (autumn 2026)

Status: **v2 — owner decisions incorporated (see §0). No code is written yet.** Source requirements: `../03-federal-lab-autumn-2026-plan.md`, the task brief, the spring pipeline `../cosmos/spacemonkeys`, the reference architecture `../../metadocs`.

## 0. Decisions taken (round 1)

| Topic | Decision |
|---|---|
| UI | **React** (Vite + TypeScript). It is the owner's first React project, so while coding I explain each concept as it appears (components, state, data fetching, routing, auth flow) in short notes in `web/README.md` and in chat. |
| Language | English for UI, code, docs, comments. Prompts to LLMs stay in Russian (works are in Russian). |
| Moodle | Files first (zip + CSV). The code keeps `MoodleSource/MoodleSink` interfaces so Web Services can be plugged in later without touching the pipeline. |
| Database | Postgres in a container (compose service with a named volume + scheduled dump to S3). |
| Hosting | New VM and new buckets at **T1 Cloud** (S3-compatible storage). Behind **nginx** with HTTPS. |
| Anonymization | Optional pipeline step after text extraction (stage 2), before any LLM call; per-run switch. |
| OCR | Opt-in per run (default off). |
| Auth | **Yandex ID (Yandex Passport) OAuth** for app users. |
| Cloud.ru | Prices taken from the catalog page (§8). |
| Round 2 (21.10) | Buckets: `fedlab-grade-data` (input **and** intermediate/cache under prefixes) and `fedlab-result-data` (final exports); endpoint `https://api.s3.dp.t1.cloud`. VM `fedlab-01` (Ubuntu 24.04), domain `aigrade.tech`. Admin: `vgarshin@yandex.ru`. Run budget cap 1000 ₽. Secrets file created: `config/secrets.json` (§16). |
| Still needed from you | The three docx attachments and the exact Moodle/output format (you said yes; I will read them as soon as they are in a folder — §15). |

## 1. Goal and constraints

Grade student **team** works (PDF slide decks from Moodle) with LLMs: five stages, every intermediate result stored, fully transparent, cheap and fast. Results go back to Moodle.

Hard dates: prompt preview **19.10**, uploads close **1.11**, grading **2–6.11**. Real data only arrives ~1.11, so everything must be tested on spring data and synthetic works first.

Fixed by the lab rules (from the 03-plan; they shape the pipeline):
- Six criteria, equal weight, total 0–100, integers. The total is computed by **code**, not by the LLM.
- AI-detection is informational only: never lowers the score, never removes a work.
- Limit 10 000 chars with spaces. A work is excluded only if **all slides are empty**.
- No usable text layer in the PDF → 0 points with a reason code, **no LLM call**, no OCR (OCR stays as an optional switch, off by default).
- Ties and ranking are outside the system (jury), but the system flags tied groups.
- Main model DeepSeek V4 Flash (Yandex AI Studio, temperature 0, reasoning off); Cloud.ru as an optional second model, compared on divergence.

## 2. What we reuse and what we fix

| Source | Take | Fix |
|---|---|---|
| `cosmos` (spring) | Stage order; Moodle input layout (`<assignment>/<Team>_<id>_assignsubmission_file/*.pdf` + grading worksheet CSV); prompt style (audit → ceilings → element count); per-team output files (`_text`, `_detection`, `_feedback`, `_feedback_long`) + aggregate JSONs | It held everything in memory dicts and re-ran a whole stage after a failure; endless `while True` retry; no cost tracking; credentials file in project; scores parsed from free text with a regex |
| `metadocs` | Python 3.12 + uv; FastAPI + worker + Redis; `LayeredStorage` (disk → S3); LLM cache keyed by model + prompt-version + input hash; prompts in files with history; `secrets.json` via compose secrets; usage/cost events; `reasoning_effort: none` finding; nginx/HTTPS deploy | Streamlit-only UI → here the UI is a separate app over the API; RQ is sync → async worker; no relational DB for status → add Postgres |

## 3. Architecture

```
 Moodle (zip + CSV) ──► [ web UI ]  ◄── users: admin / viewer
                          │  HTTPS (REST + SSE)
                          ▼
                     [ api ]  FastAPI, async, JWT auth, RBAC
                      │   │
         Postgres ◄───┘   └──► Redis (queue, rate limits, pub/sub for live progress)
         (state, config,            │
          prompts, usage)           ▼
                              [ worker(s) ]  asyncio, N concurrent tasks
                                │      │
                 T1 Cloud Object Storage  LLM providers (OpenAI-compatible):
                 (S3-compatible; source   Yandex AI Studio · Cloud.ru Foundation Models
                  of truth for artifacts) (+ Yandex OCR, optional)
```

Docker Compose services: `nginx` (only public entry: 80/443; serves the built React files and proxies `/api` to `api`), `api`, `worker` (scalable), `web` (build stage only: Vite builds static files into the nginx image; a Vite dev server runs in the dev override), `postgres`, `redis`. Secrets via compose `secrets:` files as in metadocs; never in git or image. TLS via Let's Encrypt (certbot, as in metadocs `deploy/`). This needs a **domain name** for the VM — it is also required by Yandex OAuth (HTTPS redirect URI).

Storage is accessed only through an S3 abstraction (endpoint, region, bucket names, keys from `settings.json`/`secrets.json`), so T1 Cloud vs Yandex Object Storage is a configuration difference, not a code one. Yandex AI Studio and Cloud.ru are called over the internet from the T1 VM.

### Technology choices (recommendation, to confirm)
- **api / worker / core**: Python 3.12, FastAPI, pydantic v2, SQLAlchemy 2 async + Alembic, `asyncpg`, `redis.asyncio`, **arq** as the async queue (native asyncio, tiny; RQ is sync), `openai` SDK (AsyncOpenAI) for both providers, `boto3` wrapped in a thread executor (or `aioboto3`), PyMuPDF for PDF.
- **web**: **React 18 + TypeScript + Vite**, React Router (pages), TanStack Query (server state, polling/refetch), plain CSS in M1 (Tailwind/shadcn dropped to keep the first-React learning surface small; revisit if the UI grows), the browser's built-in PDF viewer in an iframe in M1 (`react-pdf` later if page-level highlighting is needed), a diff component for prompts (M2), `EventSource` for live progress. API response types are hand-written in `web/src/types.ts` for M1 (generation from the OpenAPI schema with `openapi-typescript` is planned, so frontend and backend cannot drift silently). Built to static files served by nginx. Since this is a first React project, the plan keeps the front-end deliberately plain: no Redux, no SSR/Next.js, no heavy state libraries.
  - Schedule risk: React costs more time than Streamlit. Mitigation: build the UI in vertical slices (login → runs list → team view → run dashboard → prompts → usage), each usable on its own, and keep the REST API complete from M1 so grading can be driven via CLI/Swagger `/docs` if the UI slips.
- **Why Postgres in addition to S3**: transparency needs fast queries ("which teams failed stage 4?", "cost of run 7", progress counters) that listing S3 prefixes cannot give. S3 holds the artifacts and is the source of truth for *content*; Postgres holds *state* and can be rebuilt from S3 manifests (see §5).
- Code lives in `fedlab/`: `core/` (pipeline, no web deps, runnable from CLI/tests), `api/`, `worker/`, `web/`, `config/`, `prompts/`, `tests/`, `docs/`, `docker-compose*.yml`. Follows AGENTS.md style: type hints, async I/O, Google docstrings, no hard-coded credentials/regions/names.

## 4. Domain model

- **Run** — one grading campaign over one Moodle assignment: config snapshot (models per stage, prompt versions, limits, budget), status, totals. Config is frozen at start → reproducible and cache-safe.
- **Team / Submission** — team name, Moodle submission id, file key, sha256, page count.
- **StageItem** — (run, team, stage) with `status` (`pending | running | done | failed | skipped`), attempts, error, artifact keys, timings, cache hit flag.
- **LlmCall** — every call: provider, model, prompt version, tokens in/out, latency, cost ₽, cache hit, request/response keys in S3.
- **PromptVersion** — name, version (content hash), text, author, created, `draft | published`.
- **ModelEntry** — provider, model id, price in/out per 1k tokens, context, supports vision, max concurrency, enabled.
- **User** — Yandex ID `uid`, login, email, display name, role (`admin | viewer`), `active`, last login. No passwords stored.

## 5. Storage layout and idempotency (key requirement 1)

Buckets (T1 Cloud): `fedlab-grade-data` with prefixes `input/<run_id>/` (Moodle zip/CSV, extracted PDFs — written once, never overwritten), `_work/<run_id>/` (intermediate artifacts) and `_cache/` (LLM cache); `fedlab-result-data` (stage-5 export only). Two buckets, not three: prefix-level rules replace bucket-level ones. Names are config; the existing Yandex `cosmo-gsom-*` buckets are only a reference/test-data source (spring data is copied once into the new input bucket for tests). T1 Cloud S3 endpoint, region and addressing style (path vs virtual-host) to be confirmed when the account is ready; `boto3` works with any S3-compatible endpoint.

```
fedlab-grade-data/_work/<run_id>/<team_slug>/
    s1_upload.json                    # source key, sha256, size, pages
    s2_pages.json                     # text per page (slide numbers)
    s2_metrics.json                   # chars w/ & w/o spaces, fonts, sizes, empty slides, links, overflow, density
    s2_precheck.json                  # disqualification code if any (empty / no text layer / ...)
    s2b_anonymized.json               # optional: text with PII replaced by tokens + mapping (only if enabled)
    s3_ai_detect.<model>.json         # probability, signals, raw response key
    s4_grade.<model>.json             # schema-validated JSON (6 scores, feedback, flags)
    s4_grade.<model>.raw.txt          # raw LLM answer
    manifest.json                     # LAST write per team+stage: inputs hash, config hash, outputs, status
fedlab-grade-data/_cache/llm/<provider>/<model>/<prompt_hash>/<input_sha256>.json
fedlab-result-data/<run_id>/...            # stage 5 files
```

Rules:
1. **One work = one atomic unit.** Each stage handler: read inputs → compute → write artifacts → write `manifest.json` last. An item is *done* only if its manifest exists and its `inputs_hash + config_hash` matches.
2. **Resume = skip done items.** The orchestrator enumerates items, skips matching manifests (checked in Postgres, verified against S3 on resume), runs the rest. A crash at team 17 of 60 re-runs only team 17 onward.
3. **Deterministic keys, overwrite-safe.** Re-writing the same key with the same content is harmless; workers are at-least-once.
4. **LLM response cache** keyed by (provider, model, prompt hash, params, input hash). Changing a prompt/model automatically invalidates; re-running identical config is free.
5. **Stage-level "force"** in the UI: re-run a stage for selected teams (new `attempt`, old artifacts kept under `.../attempt-N/`).
6. **Rebuild**: `core.reindex` reconstructs Postgres state from S3 manifests (disaster recovery, and why S3 is the source of truth).
7. Bounded retries with exponential backoff and jitter, then `failed` with the error stored; no infinite loops. Failed items never block the others.

## 6. Pipeline stages

**Stage 1 — Upload / ingest.** Admin uploads the Moodle "download all submissions" zip + grading-worksheet CSV in the UI (or points to files already in the bucket). The API streams to `fedlab-input`; the worker unpacks, maps folders → teams (group/identifier ↔ folder name, reusing the spring `group_mapping` logic but with explicit, visible mapping and a table of unmatched teams), records sha256/size/pages. Duplicate resubmissions: latest by Moodle timestamp, others kept and flagged.

**Stage 2 — Extraction (code first).** PyMuPDF per-page text with slide numbers; metrics: chars with/without spaces (rule uses *with*), font sizes and families, links, empty slides, text outside page bounds, text/image density. Pre-check: all slides empty → excluded; no/garbled text layer → 0 + code, LLM skipped. Yandex OCR is an opt-in per run for scans (page-level cache like metadocs; priced and shown in the cost estimate). Optional page renders (PNG) stored for criterion 6 and for the UI viewer.

**Stage 2b — Anonymization (optional, per-run switch, default off).** Runs after extraction/pre-check and before every LLM call. Replaces personal data in the page text with stable tokens (`[PERSON_1]`, `[EMAIL_1]`, `[PHONE_1]`, `[ORG_1]`): first deterministic rules (regex for e-mails/phones/links, the team's own member names from the Moodle CSV, team name), then optionally a local NER step (Natasha/Slovnet for Russian names — a small dependency, no external call) — *not* an LLM, so no data leaves the VM. The mapping stays in S3 only; stage 3/4 read `s2b_anonymized.json` when enabled, and the `LlmCall` input hash depends on it. Feedback is de-tokenized before export. Trade-off shown in the UI: anonymization slightly changes text (character counts for the 10 000 limit are always computed on the original text in stage 2).

**Stage 3 — AI detection (informational).** LLM with the spring `ai_detect` prompt (versioned, editable). Output: probability 0–1, per-signal scores, short rationale; stored, shown, exported; has **no effect** on scores. Can be switched off per run to save cost.

**Stage 4 — Grading.** LLM gets: slide-numbered text, stage-2 metrics, case rules + six criteria (in the prompt). Output is JSON checked against a pydantic schema (team_name, disqualified/code, six integer scores, per-criterion feedback, overall feedback, technical metrics, needs_human_review). Invalid → one repair retry with the validation error, then `failed`/`needs_human_review`. **Code** computes `total_score_100`, clamps ranges, flags ties. Options per run: repeats (k runs of the same work for reproducibility stats), second model (Cloud.ru) with divergence threshold → `needs_human_review`. Criterion 6 may use a vision model on page renders; text-only baseline first.

**Stage 5 — Export.** Written to `fedlab-result-data/<run_id>/`: (a) Moodle grading-worksheet CSV with Grade + Feedback comments filled, in the exact column format Moodle re-imports; (b) per-team feedback files (short + long, as in spring); (c) `grading_results.json`, `detection_results.json`; (d) jury xlsx (scores per criterion, AI probability, flags, model divergence, ties); (e) `run_report.json` with config snapshot, cost, timing. Export is a pure function of stored stage-4 results → re-runnable at any time; manual admin overrides of scores are stored separately and win in export (with audit trail).

## 7. Models, prompts, user control

- **Provider abstraction**: one `LlmClient` interface, two OpenAI-compatible adapters. Yandex: `https://ai.api.cloud.yandex.net/v1`, model `gpt://<folder_id>/<model>` (as in cosmos/metadocs). Cloud.ru Foundation Models: OpenAI-compatible endpoint (base URL and auth scheme are **not stated on the catalog page** — to be verified in the Cloud.ru console/docs before M2; the model catalog is listed in §8).
- **Model registry** (admin UI): add/disable models, price per 1k tokens, concurrency limit, vision flag. Per run and per stage the admin picks a model from the enabled list; no model names in code.
- **Prompt management**: prompts stored as versioned text (DB, mirrored to S3 and exported to `prompts/*.md` for git). UI: list → edit (draft) → diff vs published → **test on 1 selected team** (playground, shows exact input, output, tokens, cost) → publish. A run pins prompt versions. Variables (`{{case_rules}}`, `{{criteria}}`, `{{slides}}`, `{{metrics}}`) rendered by code; static part placed first to benefit from provider prefix caching if available.

## 8. Speed and cost management (key requirement 2)

- Asyncio worker; `asyncio.Semaphore` per provider/model (concurrency from registry), client timeout (metadocs used 60 s), jittered retries on 429/5xx, global token-bucket in Redis shared by scaled workers. Time per run ≈ slowest call, not the sum.
- Code-first: everything computable without an LLM (metrics, limits, empty slides, text-layer check) is done by code; invalid works never reach the LLM.
- `reasoning_effort: none` for DeepSeek (metadocs measured 4–8× faster and fewer tokens); temperature 0.
- **Cloud.ru prices** (catalog page, ₽ per 1M tokens, input / output; context): DeepSeek-V4-Flash 43.3 / 86.6 (1048K) · DeepSeek-V4.1-Flash 64.9 / 194.8 (1048K) · Kimi-K2.6 175.7 / 725.9 (262K) · GigaChat3.5-432B-A28B-Reasoning 96.2 / 288.6 (262K) · GigaChat-3-Pro 73.0 / 176.4 (262K) · gpt-oss-120b 15.9 / 61.0 (131K) · GLM-4.6V 58.9 / 176.8 (131K, vision by name) · DeepSeek-OCR-2 54.9 / 61.0 (OCR-type model). The catalog also carries GPT-5.x, Claude 4.x and Gemini models at much higher prices. Prices are copied into the model registry as editable data with a "last checked" date, not hard-coded.
- **Order-of-magnitude cost per work** (assumption: ~10k chars ≈ 4–5k tokens of slide text + ~4k tokens of prompt/rules → ≈ 9k input, ≈ 1.5k output): on DeepSeek-V4-Flash at the Cloud.ru price ≈ 0.4 + 0.13 ≈ **0.5 ₽ per grading call**; Kimi-K2.6 ≈ 2.7 ₽. For ~100 works × (detection + grading) × 2 models this is tens to low hundreds of rubles in total — so cost is dominated by *re-runs and experiments*, not by the production pass. Yandex AI Studio rates will be read from the registry too (metadocs used 0.3–0.5 ₽ per 1k tokens for DeepSeek on Yandex, i.e. several times the Cloud.ru list price — worth re-checking, it may change the choice of the primary provider).
- Cloud.ru also lists DeepSeek-V4-Flash itself, so a cross-check can be "same model, other platform" (isolates platform noise) or "different model" (Kimi-K2.6 / GigaChat 3.5) — the registry supports both, and the test plan (§12) compares them.
- **Cost estimate before start** (dry run): tokens ≈ chars/3.5 × price from registry, per stage and model, shown for confirmation. **Hard budget cap** per run: the worker stops scheduling new calls when reached.
- **Live cost**: every `LlmCall` has tokens and ₽; dashboard by run / stage / model / day; cache hit rate.
- Cache makes re-runs after prompt-less changes (export, UI fixes) free; prompt change re-pays only the affected stage.
- Optional cheap-first routing (later): small model to screen, big model only for borderline works. Not in v1.

## 9. Transparency UI (separate web app)

**Authentication — Yandex ID (Yandex Passport) OAuth.** Authorization-code flow handled by the **API** (the browser never sees the client secret): `GET /api/auth/login` → redirect to `oauth.yandex.ru/authorize` (with `state`, optional PKCE) → `GET /api/auth/callback` exchanges the code for a token, fetches `login.yandex.ru/info` (uid, login, default_email, name), then the API issues its own short-lived session in an `HttpOnly; Secure; SameSite=Lax` cookie (+ CSRF token for state-changing calls). Yandex ID proves *who* the person is; *what they may do* is ours: a user must exist in the `users` table (pre-added by an admin by e-mail/login, with role) — anyone else gets "no access", so a random Yandex account cannot enter. The first admin is bootstrapped from `settings.json` (`bootstrap_admin_emails`). Requires: an OAuth app registered at oauth.yandex.ru (client id/secret in secrets, redirect URI `https://<domain>/api/auth/callback`, scopes `login:info login:email`). Local dev uses a dev-login switch that exists only when `ENV=dev`. Roles are enforced in the API on every endpoint; the React app only hides buttons.

Roles: **admin** (everything: upload, start/stop/resume runs, edit prompts, models, users, override scores, force re-run, budget) and **viewer** (read-only: runs, progress, all artifacts, costs, exports download).

Pages: Runs list → **Run dashboard** (stage × status matrix, per-stage progress bars, ETA, live cost, errors, "retry failed" / "resume") → **Team view** (PDF page viewer next to extracted text, metrics, pre-check verdict, AI-detect result, per-criterion scores and feedback, raw LLM request/response, model comparison, history of attempts) → Prompts → Models → Usage & cost → Exports → Logs → Users (admin). Live updates via SSE from Redis pub/sub.

## 10. Moodle integration

- **v1 (in scope for 2.11): file-based** — in: submissions zip + grading worksheet CSV; out: worksheet CSV for re-import + feedback files. This mirrors what worked in spring and needs no Moodle admin rights.
- **v2 (optional, decide with the lab): Moodle Web Services** — `mod_assign_get_submissions` / `get_participants` to pull, `mod_assign_save_grade` to push grades+feedback. Needs a service token with assign capabilities and a go-ahead from the Moodle admins. The adapter sits behind a `MoodleSource/MoodleSink` interface so v1 → v2 does not touch the pipeline.

## 11. Security and data protection

- Secrets only in `config/secrets.json` (gitignored, compose secrets), pydantic `SecretStr`, masked in logs; separate dev/prod keys; least-privilege service account per bucket.
- No passwords in the system (Yandex ID OAuth); server-side sessions in HttpOnly cookies, CSRF protection, role checks in the API, audit log of admin actions.
- Student data goes to external LLM APIs: confirm Yandex/Cloud.ru terms (flagged in the 03-plan) before real data; optional anonymization step 2b.
- VM hardening: only 80/443 (+ SSH restricted) open in the T1 security group; Postgres/Redis not exposed; nginx security headers and rate limiting on `/api/auth`; automatic security updates; Postgres dump to S3 nightly.
- No public bucket access; input bucket write-once.

## 12. Testing and validation

- Fixtures: spring data (34 teams, ~260 objects in `cosmo-gsom-check-data-2`, read via `yc`) plus synthetic works for edge cases (empty slides, no text layer, >10k chars, tiny fonts).
- Unit tests for metrics/pre-check/score arithmetic/schema validation; integration tests with a fake LLM server; kill-and-resume test (kill the worker mid-stage, assert only unfinished items re-run, no duplicates); contract tests for both LLM adapters (mocked).
- Quality: reproducibility (k repeats, score variance), model divergence, manual comparison with jury on 1–2 real works (plan step 8).

## 13. Milestones (today is 18.10)

| # | When | Deliverable |
|---|---|---|
| M0 | 18–19.10 | This plan agreed; answers to open questions; prompt v1 draft (separate track, by you) |
| M1 | → 22.10 | Skeleton: compose + nginx, Postgres/Redis, Yandex ID OAuth + roles, storage layer (T1 S3), stages 1–2 + manifests/resume, full REST API skeleton, React shell (login, runs list, team view) |
| M2 | → 26.10 | Stages 2b, 3–4 with Yandex (and Cloud.ru once the endpoint is verified), schema validation, cache, usage/cost tracking, prompt editor + playground, test on spring data |
| M3 | → 29.10 | Stage 5 + Moodle CSV, run dashboard with live progress (SSE), budget cap, resume/force, jury xlsx, VM + domain + HTTPS deployed |
| M4 | → 31.10 | Dry run on synthetic + any real early submissions; load test (60+ works); runbook |
| M5 | 2–6.11 | Production grading; Cloud.ru second model enabled if access confirmed (can slip after 6.11 without blocking) |

Cuts if time runs short: UI polish (keep API + Swagger as the fallback driver), Moodle WS (stay files), vision model for criterion 6, repeats, OCR, NER in 2b (keep regex rules only).
Note: the VM, domain and OAuth app have lead time outside the code — request them **this week** (see §15).

## 14. Risks

| Risk | Mitigation |
|---|---|
| Yandex performance problems (noted in the 03-plan) | Concurrency/timeout tuning, cache, resume, Cloud.ru adapter ready as fallback |
| Moodle CSV format drift | Golden-file test from spring worksheet; Moodle-format validator before export |
| Partly garbled text layer (open question in 03-plan) | Heuristic quality score in stage 2 → `needs_human_review`, not 0, until the rule is clarified |
| Prompt not ready on time | Prompts are data, not code; pipeline can be finished independently |
| Cloud.ru access/terms unknown | Optional; adapter behind interface; verify early |
| LLM score instability | temperature 0, schema validation, repeats and 2-model divergence → human review |

## 15. Open items (as of 21.10)

**Blockers for you**
1. **DNS**: `aigrade.tech` currently resolves to `95.163.244.138`, but `fedlab-01` is `193.246.150.117`. Point the A record to the VM (TTL low), otherwise Let's Encrypt and the OAuth redirect will fail. Is `95.163.244.138` something else you use?
2. **Docs**: put the three docx files into `fedlab/_reqs/` (gitignored). Prompt v1 and the stage-4 schema wait for them.
3. **T1 keys**: fill `config/secrets.json` → `object_storage.access_key_id` / `secret_access_key` (see §16). I also need the **S3 region** (T1 may require a specific one; `settings.json` → `object_storage.region`).
4. **Yandex OAuth app** at oauth.yandex.ru under `vgarshin@yandex.ru`: callback `https://aigrade.tech/api/auth/callback`, scopes `login:info login:email`; put id/secret into `secrets.json` → `yandex_oauth`.
5. **LLM keys**: `yandex_llm.api_key` (+ folder id in settings), later `cloudru_llm.api_key` and its base URL.

**VM findings (`fedlab-01`, checked read-only over SSH)**: Ubuntu 24.04, **2 vCPU / 3.8 GB RAM**, 61 GB disk, sudo works, **Docker not installed**. 2 vCPU / 4 GB is enough for compose (api + worker + postgres + redis + nginx) because the heavy work is remote LLM calls, but it is tight: Postgres memory capped, one worker process with async concurrency, PDF rendering done page-by-page. If the 2–6.11 load test shows pressure, resize to 4 vCPU / 8 GB. I'll write `deploy/bootstrap-server.sh` (Docker, ufw 22/80/443, unattended upgrades, 2 GB swap) for you to review before running.

**Defaults assumed** (object if wrong): admin score override with audit trail; stage-2b anonymization covers people and contacts only; run budget cap 1000 ₽ (in `settings.json`).

## 16. Secrets and config files (created)

- `config/secrets.json` — **fill this one**; `chmod 600`, gitignored, mounted by compose as a secret, never copied into images. Sections: `object_storage` (T1 key id + secret), `yandex_llm`, `yandex_ocr`, `cloudru_llm` (api keys), `yandex_oauth` (client id/secret), `app` (`session_secret`: any random 32+ bytes, `postgres_password`). Generate the two app values with `openssl rand -hex 32`.
- `config/secrets.example.json` — template in git.
- `config/settings.json` — non-secret: domain, bootstrap admin, endpoint, bucket names, budget cap. Empty: `region`.
- I do not read `secrets.json`; I check access by running code. Do not paste keys in chat.

## 17. Findings from the lab prompts and test decks (22.10)

Source: `_reqs/prompts/` (long ≈ 100k chars, short ≈ 26k chars; the case is **«Молодёжный Банк ВТБ»**, audience 14–25, chain Table 1 analysis of 5 banks → Table 2 four new products → Table 3 four promotions) and `_reqs/tests/` (5 PDFs).

**Contract fixed by the prompts (becomes the stage-4 pydantic schema, no invention needed)**
- Output: one JSON, `status` = `EVALUATED | DISQUALIFIED`; `scores.criterion_1..6` each `{name, score 1–10 int, evidence[{slide, description}], feedback}`; `sum_score_60`, `total_score_100`, `general_feedback`, `technical_warnings[]`, `needs_human_review`, `disqualification_code/reason/evidence`.
- **Arithmetic is ours**: `sum_score_60` = sum, `total_score_100` = round(sum/60×100). The model's numbers are recomputed and a mismatch is logged. Disqualified → 0.
- Technical filter (code, stage 2) codes: not PDF · anonymous (no team name / author) · not machine-readable. Content codes from the LLM only: `D1_ALL_CONTENT_EMPTY`, `D2_OFF_TOPIC`, `D3_NONSENSE_OR_IRRELEVANT`, `D4_STRUCTURE_ONLY`. A technical code is passed in the input and the LLM must keep it; we skip the LLM call entirely in that case.
- Input to the model: team name, slide-numbered text, precomputed metrics (is_pdf, machine-readable, chars with spaces, slide count, main font size, number of font families, text overflow, empty slides, URLs, QR codes, external-file references, images). Over 10 000 chars is only a warning → criterion 6.
- Prompt-injection rule is in the prompt; we additionally wrap slide text in clear delimiters and never put it in the system role.
- **The AI-detection prompt is not in the pack.** The grading prompt explicitly forbids judging AI authorship, so stage 3 stays a separate call with its own prompt (the spring `ai_detect` prompt as seed). Needs your confirmation.

**Consequences for the design**
1. Seed the prompt store with two versions: `grade_long_v1` and `grade_short_v1` (imported from the docx, text kept verbatim). A/B between them on the test decks is the first experiment: long ≈ 35–40k tokens vs short ≈ 9–10k. At Cloud.ru DeepSeek-V4-Flash prices (43.3 ₽/1M in) that is ≈ 1.6 ₽ vs ≈ 0.4 ₽ per work *before* output; still cheap, but a 4× difference in latency and cost for every re-run. Provider prefix caching on the static prompt part is worth testing (the rules come first, the work last).
2. The prompt expects the **same metrics** we planned in stage 2 plus QR codes and external-file references: add QR detection (decode page images, e.g. OpenCV `QRCodeDetector`) and URL/"see attached file" text patterns to stage 2.
3. Criterion 6 is explicitly limited to metrics and text structure ("no page images → do not invent colors"), so **vision is out of v1**. This removes the Gemma/vision question from the critical path.
4. `needs_human_review` is only for technical uncertainty, so divergence between two models gets its **own** flag (`models_diverge`) and does not overwrite it.
5. Score range is 1–10 per criterion (not 0–100 per criterion): the divergence threshold for two models is defined on `sum_score_60` (default ≥ 6 points, configurable) plus any single criterion differing by ≥ 3.
6. Participant-facing feedback has forbidden words («проблема», «ошибка», «балл», «критерий» …). Add a **post-check in code** that flags those words in the model output and triggers one repair retry — cheaper and more reliable than hoping the model obeys.

**Test decks** — measured with the real stage-2 extractor (whitespace-normalised chars with spaces): 1 → 16 slides / 8 941 chars; 2 → 9 / 3 645; 3 → 6 / 1 344 ("bad, other topic": expected `D2_OFF_TOPIC`); 4 → 5 / **10 181** (PowerPoint, Calibri; the only one over the 10 000 limit); 5 → 4 / 9 016 (PowerPoint). *Correction:* an earlier note in this section said three decks exceed the limit; that came from `pdftotext -layout`, which counts layout whitespace. The normalised count is the right one for the lab rule. None of the five has URLs, QR codes, overflow, empty slides or images, so those paths are tested with synthetic PDFs. We still lack an anonymous deck, a scanned deck and an all-empty deck: generated synthetically in `tests/`.

## 18. Milestone 1 status (22.10)

Done and verified: compose stack (api, worker, postgres, redis; nginx/certbot in the prod file), config + secrets loading, S3 layer against T1 (`python -m core.s3check`: both buckets OK), manifests/resume, stage 1 (Moodle zip + worksheet match), stage 2 (text, metrics, QR, URLs, external refs, pre-check, opt-in OCR with page-level cache), Yandex ID OAuth + roles + CSRF guard, REST API + SSE, React shell (login, runs, run dashboard with live progress, team view, users), 21 automated tests, an end-to-end run on the 5 test decks through real Postgres/Redis/arq/T1 S3 including the "DB state lost → resume from S3 manifests" case.

Not done yet (by design): stages 2b, 3, 4, 5, prompt store/editor, model registry, cost tracking, deployment to `fedlab-01` (scripts are written but not executed: they change the server, so they wait for your go).
Known gaps: no Alembic yet (tables via `create_all`), OAuth flow untested against real Yandex (needs the deployed domain), OCR path tested only with a fake client, `Run` status does not yet reflect later stages.

## 19. Milestone 2 status (22.10)

Done and verified: LLM gateway (Yandex AI Studio; Cloud.ru adapter present but disabled until endpoint+key), model registry (DB, prices editable in UI), prompt store (immutable versions, draft → published, diff, playground), seeded with `grade_long_v1`, `grade_short_v1` (verbatim from the docx) and `ai_detect_v1` (written by us), stage 3 (AI detection, informational), stage 4 (grading: JSON contract validation, code-computed totals, forbidden-word check with one repair, technical-FAIL → no LLM call), per-run **variants** (several prompt×model combinations graded side by side; first = primary), cost tracking per call, budget cap enforced before each paid call, pre-run estimate, React pages (run configuration & cost, grade/AI views per team, prompts, models). 34 automated tests; real end-to-end run on Yandex DeepSeek V4 Flash over the five test decks.

First real comparison (5 decks, same model): short vs long prompt totals 82/78, 43/43, 0/0 (both `D2_OFF_TOPIC`), 73/68, 67/68. Long prompt ≈ 2× the cost and tokens of short. Reproducibility across repeated runs and Yandex-vs-Cloud.ru comparison are still to be measured.
Open items: Yandex prices in the registry are placeholders copied from metadocs (300/500 ₽ per 1M tokens): verify; real Yandex folder id is now `b1gerja4eh37e1jfs0fp` (from the brief); stage 2b, stage 5, Cloud.ru wiring, models_diverge flag, reproducibility experiment.

## 20. Milestone 3 status (23.10): ingestion paths, canonical tree, stage 5, Cloud.ru, migrations

* **Ingestion** (see `docs/STORAGE.md`): UI zip+CSV upload, AWS-CLI zip into `incoming/<batch>/`, AWS-CLI unzipped folders into `incoming/<batch>/` — all produce the cosmos tree under `input/<run>/`. Verified with the real AWS CLI against T1 (5 teams, identical trees from zip and from unzipped folders), plus automated tests incl. idempotency and two runs from one batch.
* **Stage 5**: result tree in cosmos layout with `<run>` kept; worksheet CSV filled (Grade, Feedback comments; delimiter and BOM preserved); per-team `_grading_result_feedback(.long).txt`, `pipeline_results/…`, aggregate JSONs, `jury_report.xlsx`, `run_report.json`. Ties are listed (not decided); variants are compared (`models_diverge` when sums differ by ≥ 6/60 or a criterion by ≥ 3).
* **Cloud.ru** works through the same gateway (base URL `https://foundation-models.api.cloud.ru/v1`, ids from `/models`, API key accepted). First comparison on 5 decks: Cloud.ru DeepSeek V4 Flash grades higher (93/52/0/77 vs 82/43/0/67) and uses 2–7× more completion tokens at default reasoning (67 s per call vs 25–30 s). Its `reasoning_effort` accepts low/medium/high (not "none"); the registry now sets `low`. A like-for-like re-measurement is still to do.
* **Yandex prices** confirmed from the official list (0.3 / 0.075 cached / 0.5 ₽ per 1k tokens); cached-token billing is implemented (the repeated static prompt prefix is billed at the cached price when the provider reports it).
* **Alembic** migrations replace `create_all` (baseline `0001`); api and worker migrate at start under a Postgres advisory lock.
* Stage start now enqueues all teams and lets the S3 manifests decide (so adding a variant to a finished run computes only the new variant — verified).
* Known gaps: export writes files sequentially (≈12 s for 5 teams; to parallelise for ~100 teams); admin score override; stage 2b; reproducibility experiment; participant-facing wording of the score line is copied from cosmos ("Тестовый балл") and configurable in `settings.export.score_line`.

## 21. Simplification (23.10, evening): one model, one prompt, no comparisons

Decided by the owner after the 4-variant demo (Yandex vs Cloud.ru, long vs short prompt): **one default model and one default prompt per stage, no comparison artefacts.** Defaults: grading = `grade_short` × DeepSeek V4.1 Flash (Yandex AI Studio); AI detection = `ai_detect` × the same model. The demo run and its files were deleted.
* Variants, the compare view/API, divergence flags and the xlsx jury report are removed; the jury report is `jury_report.csv` only.
* Model, prompt and the **AI-detection on/off switch** live on the **Settings** page (global); the **Models** page only switches models on/off. The latest *published* version of the chosen prompt is used when a stage starts; changing a choice makes the next run of a stage recompute only what used the old choice (manifest config hash).
* Run page: per-stage Resume/Run buttons stay; new **Resume / run all stages** (2 → 3 if on → 4 → export, per work, skipping finished work). Stage 1 starts by itself after an upload or *Use this batch*; everything after is a manual click. The Stage 1 card stays available to **re-read the source** (late files in the same batch folder; stored works are skipped).
* Cloud.ru stays wired (models can be enabled on the Models page and chosen in Settings) but is not used by default.

## 22. Run page cleanup, budget and analytics (24.10)
* **Configuration & cost** card is information only. **Budget cap** is now one global setting (Settings → *Budget limits*, default 1000 ₽) covering grading, AI detection **and OCR** (OCR pages are recorded as paid usage). **OCR toggle** exists only in the Stage 2 block.
* OCR rule (superseded 25.10, see §23): page-level.
* Per-work result files are listed on each work's page (before *Extracted text by slide*); the export card keeps only the run-level files.
* New **Analytics** page: run filter (latest by default), KPIs (teams, members from the worksheet, graded / disqualified / not graded, average and median grade, money spent), histograms of grades and of lengths (with the 10 000-character limit marked), top-5 and bottom-5 graded works. Charts are dependency-free SVG with a table view.

## 23. OCR per slide, caches and Settings blocks (25.10)
* **OCR is page-level** (when the run's OCR toggle is on): only slides without usable text of their own (empty, or mostly unreadable symbols) are recognised; slides with a text layer are kept. A PDF can therefore be `mixed` (text + OCR). Metrics keep the font data of the text slides; `ocr_pages` lists the recognised slides and a warning `OCR_PAGES` is added. Settings → **OCR**: maximum slides recognised per work (default 60); a work needing more skips OCR (warning `OCR_SKIPPED`). OCR cost (0.1321 ₽/slide, cached per slide) is recorded as run spend, and a work is not OCR'd if the run's budget cap would be exceeded.
* **Caches** (both in `fedlab-grade-data/_cache/`): LLM answers keyed by (model, reasoning, temperature, system prompt, user message with the work text and metrics) and OCR pages keyed by file hash + slide. A repeated identical request is served from the cache: same answer, 0 ₽. Settings → *Danger zone* clears them (typed confirmation); stored run results are untouched.

## 24. Roles, Playground, file names (26.10)
* **Result file names** no longer contain the team name: `<assignment>/<Team>_<id>_assignsubmission_file/grading_result_feedback.txt` and `…_long.txt` (the folder already carries the team). *Build/Rebuild export* now deletes files of an older export that it no longer produces, so renamed files do not linger.
* **Roles** (enforced in the API, hidden in the UI):
  * **admin** – everything.
  * **viewer** – sees runs, a single run, a single work, results and **Analytics** (with the run filter); downloads any exported file, a whole run's export as a zip, or one work's files as a zip. Cannot create runs, upload / pick a batch, start any stage or "run all", rebuild the export, toggle OCR, can use the **Playground**, and has **no access** to Prompts, Models, Settings or Users (those API endpoints return 403; the pages redirect to the start page). A permission-matrix test covers every endpoint.
* **Playground** (open to viewers since 26.10 on the owner's decision; it uses its own endpoints `/api/playground/options|run` that return prompt names/sizes and model names but never prompt texts or prices; every call is billed to the chosen run and refused with 402 once the run's budget cap is reached): choose a run (latest by default), a work (first by default), any prompt version and any enabled model (defaults from Settings); optional *fresh call* skips the answer cache; shows score table, raw answer and the exact input; saves the result (.json), the raw answer (.txt) or a full report (.json) to the user's computer. Experiments are billed to the chosen run.

## 25. API page and API keys (27.10)
* **API page** (admin only, browser login): how to call the API, **API keys** (create with name / role viewer|admin / expiry; shown once; only a SHA-256 hash is stored; revoke; last-used time), and a **Swagger-like reference** generated from the live OpenAPI schema with the role each endpoint needs (`x-role`), parameters, JSON/multipart fields, responses and a curl example. `/api/docs` stays hidden in production; the page serves the schema through `/api/meta/openapi` (admin only).
* **Key auth**: `Authorization: Bearer fl_…` or `X-API-Key`; a presented key is the only credential examined (cookie ignored, no CSRF header needed). Viewer keys are read-only; admin keys do everything except manage keys. Key management requires a browser admin session. Expired / revoked keys get 401. Tests cover the lifecycle, scopes, expiry and the reference.
* The page route is `/developers` (a path starting with `/api` would collide with the API proxy).

## 26. Design round 1 (27.10): hybrid layout
Chosen by the owner: **C – hybrid** (compact tables, roomy cards). Screenshot tooling: `scripts/screenshots.sh [name]` (Playwright in Docker; dev stack + demo run; light/dark/mobile) → `_design/shots/<name>/`. Done: run page reordered (header actions + info line, thin stage strip with the OCR switch, works table directly below, export / source files / configuration as collapsed blocks), sliding on/off switches everywhere a toggle exists (OCR, AI detection, models enabled, user active, playground fresh call, cache clearing), analytics tiles in one row and range labels on the charts, playground controls sticky and PDF/answer panes sized to the window, sticky top bar, phone-width fixes (wrapping nav, scrollable tables), dark-mode number inputs. Known limit of the screenshots: headless Chromium has no PDF viewer, so PDF panes appear blank there.

## 27. Corporate-blue style applied (28.10)
The approved proposal (`_design/proposals/vtb-style/`) is now the app's design system: tokens in `web/src/styles.css` (light + dark, Auto follows the OS), new top bar with pill navigation and a Light/Auto/Dark switch (`theme.ts`, remembered per browser), split login page, filled/outlined buttons, status chips, rounded cards with soft shadows, gradient progress bars and chart bars, highlighted KPI tiles. All pages use the same tokens. The reference site (learn.vtb.ru) could not be fetched (403), so exact colours are an approximation; send a screenshot to match precisely.

## 28. Design pass over all pages (29.10)
Every page was reviewed in light, dark and phone-width screenshots and adjusted: Runs (title + create form in one row), Settings (model/prompt card full width, other blocks in two columns), Models/Users (compact selects in tables), Analytics, Playground, work page, Prompts, API. Phone width: navigation is one scrolling row; every table sits in a sideways-scrolling wrapper; score tables on the work page and Playground become stacked cards (criterion, score, feedback, evidence).

## 29. Injection and manipulation hardening (25–26.10)
Tested with 15 synthetic works (hidden text, fake delimiters/JSON, score demands, prompt-leak requests, padding, off-topic): no score inflation beyond run noise (~±5), no prompt leak, but the pipeline was silent about attempts. Added: stage 2 scan `core/injection.py` (hidden text + RU/EN phrase patterns, warnings in `technical_warnings`, hidden from the grader's input), `ai_detect` v2 with the `manipulation_attempt` field (flag in `quality_flags` at export), `teams.review_flags` (migration 0004) and a **Flags** column on the run page, tolerant `Evidence` parsing (answers with "slide N: text" strings). Decision: these hints do not use `needs_human_review` (technical uncertainty only, §17.4). Details and results: `docs/INJECTIONS.md`; one-pager for staff in Russian: `docs/INJECTIONS_RU.md`. Open: similarity check between works, trimming of over-limit text, a score-stability check (repeats), the flag in the Grade card of the work page.

## 29. Back links, file buttons, Manual page (26.10)
* `← Runs` / `← Run` text arrows replaced by a pill with a chevron icon (`BackLink`). The browser's "Choose File" is replaced by styled upload buttons (`FilePick`, icon = arrow down onto a line) in *Source files*.
* New **Manual** page (`/manual`, every role): English and Russian side by side; separate admin and viewer versions (admins can switch to read the viewer version): how the app works, roles, every page with its key functions, a typical workflow and tips. Content lives in `web/src/manualData.ts`.

## 30. Batches: write-once uploads (28.10)
Problem: files were added to an existing batch (`demo-zip`: 2 zips + 2 CSVs). Rule now: **a batch is created once with a new name and never extended.**
* Default name `YYYYMMDD-HHMM` (local time of the browser; no colon), editable; `POST /api/batches` (reserve) and the browser upload refuse an existing name (409). `batches` table (migration 0005): name, origin ui|cli|outside, status reserved|uploaded|used, seal hash.
* The AWS CLI cannot be blocked, so violations are **detected**: a batch with 2 zips / 2 CSVs / zip + unzipped works is *invalid*; a batch whose listing changed after the first use is *changed after use*; neither can be used. Never-reserved folders show as *outside the app*.
* The Run page's *Source files* card has three modes: Upload from this computer, Copy with the AWS CLI (reserve a name, copy the commands), Existing batches (validity chips, use, delete unused). A run can hold several batches (late files = a new batch); the old `/api/runs/-/incoming` is replaced by `GET /api/batches`; `POST /api/runs/{id}/upload` now creates a named batch.
* Existing `demo-zip` is flagged invalid on production; delete it from the UI (it was never used by a run).
* Workstation note: from this PC the T1 S3 endpoint timed out during development (prod VM reaches it fine), so the batches table was checked with sample data in screenshots and the logic with 13 automated tests.

## 31. Works table fit + external-reference rule (29.10)
* The Works table no longer overflows the screen (it cut the Grade column at 1280 px): the Worksheet and Extract columns were folded into the Team cell (a "no match" chip when the team is missing in the worksheet) and the Pre-check cell (shows a status only while stage 2 is not finished); long file names are shortened with an ellipsis (hover = full name); flags wrap; tables get a sideways scroll as a safety net; the top bar stays on one row on 1024 px screens.
* `EXTERNAL_REFERENCE` (stage 2 warning + `external_ref_pages` metric passed to the grader) was far too eager: on run "Test 003" it flagged 10 of 22 works, all because they described a *product feature* ("взнос по ссылке", "оплата по QR-коду", "облачное хранилище"). The rule now needs wording that points to the work's own substance being elsewhere (cloud-drive / Figma / Miro / Notion links, "подробные расчёты по ссылке", "данные доступны по QR", "в приложенном Excel", "отдельный файл", "ссылка на презентацию"); a "ссылка на Таблицу 1" is the required cross-reference and is not flagged. On the same 22 works the new rule flags 0. Results already stored keep their old flags until stage 2 is recomputed (changing the metrics also changes the grader input, so grading would be re-paid).

## 32. PARTIALLY_READABLE flag for works with text-free slides (30.10)
Some decks mix normal slides with image-only/garbled ones (scans of a few pages). Such a work can pass the technical filter (enough text overall) and then be DQ'd by the grader for nonsense — or grade fine after OCR — and nothing explained why. New, informational only:
* `TechMetrics.no_text_slides` — slides whose **own text layer** was empty or garbled (the same predicate `pages_needing_ocr` uses to pick OCR targets), recorded **before** OCR and never recomputed by `apply_ocr`: it is a historical fact about the file.
* Pre-check warning `PARTIALLY_READABLE: slide(s) [n] have no usable text layer[; OCR recovered [n]][; no text for [n]]` — only when **some but not all** slides lack a text layer. The all-slides case stays covered by `NOT_MACHINE_READABLE` (OCR off/failed) or `OCR_PAGES` (OCR ran), so run "Test 004" `WW $WIN` (full scan → OCR → fine) keeps its current, correct behaviour.
* Surfaces: the run page **Flags** column (`s2:PARTIALLY_READABLE` via `review_flags`), the work page pre-check card, grading `technical_warnings` (jury CSV, feedback files). Like the injection hints it **never changes scores** and does not use `needs_human_review`.
* **The grader never sees it**: `no_text_slides` is in `HIDDEN_FROM_GRADER` and the warning is filtered in `build_user_message` next to `HIDDEN_TEXT`/`INJECTION_SUSPECTED`, so prompts stay byte-identical — the LLM answer cache still hits and re-grading a re-extracted work stays free and score-stable.
* Rollout to existing runs: `STAGE_VERSION` bumped to 4, so "Run" on stage 2 recomputes extraction (free; OCR pages come from the slide cache) and sets the flags; grading then recomputes from cache at 0 ₽.
* Tests: partial flag + warning text, flag survives OCR, full-scan is not partial, flag never reaches the grader prompt, `s2_flags` pick-up.

## 34. Ingest progress bar (02.11)
Large batches sat minutes on a bare `ingesting` badge. Stage 1 now reports live progress, reusing the existing Redis → SSE pipe:
* `core/stages/ingest.py` gains `IngestProgress`: thread-safe counters for the three phases — source zip **bytes downloaded** (boto3 `Callback` via a new optional `progress` argument on `Storage.get_file`), **files unpacked**, **teams registered**. Overall % weights the phases 45/40/15 (the download dominates) and normalizes over the phases a batch actually has (unzipped folders have no download phase). `snapshot()` emits flat values; `percent()` is None until something is measurable → indeterminate bar.
* `pipeline.run_ingest` runs a ticker task that publishes `{"type":"ingest", …}` to the run's Redis channel every 2 s (best-effort, like all progress events) and cancels it in a `finally`; a final `percent: 100` event goes out before `ready`.
* API unchanged: `/api/runs/{id}/events` already relays everything.
* Web: `components/IngestBar.tsx` renders one strip under the run title while `status == "ingesting"` (phase label + counts + %, 10 px bar, indeterminate animation until the first tick). `RunPage` parses `ingest` events into local state — progress ticks never trigger refetches; only real state events do. The strip disappears when the run leaves `ingesting`.
* Tests: percent math (weights, unstarted known phases, 100), end-to-end totals on a real zip via `LocalStorage` (bytes == zip size, unpack == members + csv, register == teams, percent 100), ticker skipped without redis. 81 passed.

## 33. Upload limit 1000 MB; oversized uploads fail with a clear message (02.11)
The production logs caught a real user blocked six times in a row: a ~635 MB Moodle zip ("download all submissions" of a large course) was rejected by nginx (`client_max_body_size 520m`) before the API could answer, and the browser received an HTML 413 page the UI could not render. Changes:
* `processing.max_upload_mb` = **1000** (`config/settings.json`; the code default in `core/config.py` stays 500, prod overrides it). The nginx cap is no longer hardcoded: `${UPLOAD_BODY_LIMIT}` (default **1100m**, ~10% headroom for multipart overhead), set via `UPLOAD_BODY_LIMIT` / `UPLOAD_LIMIT_MB` in `docker-compose.prod.yml`; `UPLOAD_LIMIT_MB` must match `max_upload_mb`.
* Oversized uploads never reach the API, so nginx itself answers in the API's own JSON shape: `error_page 413 = @api_413` returns `{"detail": "The file is larger than the 1000 MB upload limit…"}` and the web UI renders it as a normal error message.
* `GET /api/settings` now exposes `processing.max_upload_mb`; *Source files* shows "up to N MB" next to the zip picker and refuses to start an upload over the limit — checked client-side, before any bytes are sent (previously the browser uploaded hundreds of MB just to be rejected).
* Verified live after deploy: 667 MB and 1.05 GB requests pass nginx to the API; 1.21 GB returns the JSON 413; 78 automated tests pass. The AWS CLI batch path remains the escape hatch for anything bigger.
