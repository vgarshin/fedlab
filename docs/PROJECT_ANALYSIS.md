# fedlab project analysis

**Date:** 2026-11-02  
**Scope:** static review of the current `fedlab/` checkout: pipeline, API, worker, React application, database schema/migrations, object storage, caching, tests, and deployment files.  
**Constraints:** no application code or configuration was changed. No live database, Redis instance, bucket contents, or student records were inspected. `config/secrets.json` and `.env` were not read. Tests were not executed during this review.

This document describes what the code is designed to store and do; it does not report actual production row counts or bucket usage. `PLAN.md` is useful historical context, but code references below were checked against the implementation.

## 1. Executive summary

`fedlab` is an asynchronous AI-grading system for Moodle PDF submissions. It has a sound overall separation of concerns:

- React/Vite provides the UI.
- FastAPI provides the REST API, authentication, authorization, and SSE progress.
- `arq` workers execute ingestion, extraction/OCR, AI detection, grading, and export.
- PostgreSQL stores application state, configuration, users, prompts, model prices, and usage accounting.
- Redis stores queue/job data and transient pub/sub progress events.
- T1 Cloud S3-compatible object storage stores source files, extracted content, intermediate results, manifests, caches, and exports.

The strongest parts of the implementation are its manifest-based resume design, deterministic artifact keys, code-computed score arithmetic, technical pre-checks that avoid unnecessary LLM calls, immutable prompt versions, exact response caching, and a comparatively broad backend test suite.

The main improvement areas are production safeguards rather than a need to redesign the system:

1. Implement and rehearse database backup/restore; the planned S3-to-Postgres reindexer is not present.
2. Add atomic stage execution claims and distributed single-flight locking for identical concurrent LLM/OCR requests.
3. Make budget enforcement atomic, freeze effective model/prompt settings per stage generation, and fix the premature-export race on re-runs.
4. Add uncompressed ZIP limits and stream large downloads/exports instead of buffering them in memory.
5. Treat the `anonymize` option as unimplemented; it currently has no privacy effect.
6. Tighten cache-key completeness, batch integrity checks, environment isolation, observability, CI, and dependency reproducibility.

## 2. Architecture and processing flow

```text
Browser (React)
      |
      | HTTPS / REST / SSE
      v
nginx ----> FastAPI ----> PostgreSQL (state, prompts, users, accounting)
              |
              +--------> Redis <---- arq worker(s)
                           |             |
                           |             +----> S3 data bucket
                           |             |      (input, work, manifests, caches)
                           |             |
                           |             +----> S3 result bucket (exports)
                           |             |
                           |             +----> Yandex / Cloud.ru LLMs
                           |             +----> Yandex OCR (optional)
                           |
                           +---- arq jobs and pub/sub progress
```

### Pipeline stages

1. **Ingest:** a write-once Moodle batch is copied/unpacked into `input/<run_id>/`; teams and worksheet matches are registered.
2. **Extract:** PDF text and technical metrics are generated locally. OCR is optional and only processes slides without a usable text layer.
3. **AI detection:** optional and informational; it does not alter scores.
4. **Grading:** the LLM returns schema-constrained JSON; Python validates it and recomputes the score arithmetic. Technical pre-check failures receive zero without an LLM call.
5. **Export:** stored artifacts are converted into Moodle CSV, feedback files, aggregate JSON, jury CSV, and a run report.

Each per-team stage writes output artifacts first and its manifest last. A matching input/config hash allows resume without recomputation (`core/manifest.py:28-43`). This is a good idempotency design.

## 3. What is stored in the databases and other storage

### 3.1 PostgreSQL

PostgreSQL is the application metadata and accounting database. It uses the `pg-data` Docker volume (`docker-compose.yml:32-47`). Alembic also maintains its internal `alembic_version` bookkeeping table.

| Table | What it stores | Important notes |
|---|---|---|
| `users` | Yandex UID, login, email, display name, role (`admin`/`viewer`), active flag, creation and last-login times | No local passwords and no Yandex OAuth access tokens are stored. |
| `runs` | Run name/status, creator, note, per-run JSON config, source/batch metadata, latest export summary | `config` currently contains per-run options such as OCR; model/prompt defaults are resolved from current global settings when a stage executes. |
| `teams` | Team/submission mapping, original filename and directory, S3 file key, SHA-256, size, PDF flag, extra files, worksheet-match state | Also contains denormalized pre-check status/code, character/slide counts, final grade/status, AI probability, and review flags for fast UI queries. S3 artifacts remain authoritative for detailed results. |
| `stage_items` | One row per run/team/stage: pending/queued/running/done/failed state, attempts, error, timings, duration, and resume flag | Here `cache_hit=True` means the stage resumed from a matching manifest; it is not specifically an LLM cache hit. |
| `app_settings` | Admin-editable JSON overrides: default models/prompts, AI-detection switch, OCR page limit, run budget, and export wording/format | Values override `config/settings.json` (`core/appsettings.py`). |
| `api_keys` | Key name, visible prefix, SHA-256 hash, role, creator, creation/expiry/last-used/revocation times | The plaintext API key is shown once and is not stored. |
| `batches` | Batch name, origin, reserved/uploaded/used status, creator, and sealed listing hash/time | Actual batch files are in S3. The seal currently hashes paths and sizes, not contents. |
| `audit_log` | Actor, action name, JSON details, and timestamp for logins and administrative actions | This is an application audit trail, not a full database audit log. |
| `llm_models` | Provider/model identifiers, label, input/output/cached-token prices per 1M tokens, concurrency, reasoning effort, enabled flag, price-check date | Prices are editable data rather than hard-coded billing logic. |
| `prompt_versions` | Full prompt text, name/kind, numeric version, content hash, draft/published state, note, author, creation time | Prompt versions are immutable in normal UI flow; publishing selects which version new stage executions use. |
| `llm_calls` | Run/team/stage, model and prompt metadata, prompt/completion/cached-token counts, cost in RUB, latency, application-cache hit flag, status/error, timestamp | OCR charges are also inserted here as synthetic `yandex-ocr` usage rows. Cached application responses themselves are not stored here. |

**Not stored in PostgreSQL:** PDF bytes, Moodle ZIP/CSV contents, extracted slide text, raw LLM responses, exported files, cached LLM response bodies, and cached OCR page text. Those are object-storage artifacts. Some summary values are copied into PostgreSQL for querying.

Schema source: `core/db.py`; migrations: `alembic/versions/0001` through `0005`.

### 3.2 Redis

Redis has two actual application roles:

- **arq queue state:** queued/running/retrying jobs and related job metadata. Completed job results are not retained (`worker/main.py:70-77`).
- **Pub/sub progress:** workers publish small JSON events and the API relays them through SSE (`core/events.py`, `api/routes/runs.py:377-394`).

Redis uses append-only persistence and the `redis-data` volume (`docker-compose.yml:50-59`), but it is not the authoritative store for grades or artifacts. Queue work can be recreated by enqueueing stages again.

Redis does **not** contain the LLM response cache, OCR cache, browser sessions, or provider-side prompt cache. The current nginx rate limits are also not Redis-backed. Semaphores/rate limiters in the Python code are in-process, per-instance controls rather than distributed limits.

Browser sessions are stateless signed cookies, so there is no server-side session table or Redis session store (`api/deps.py:78-92`).

### 3.3 S3-compatible object storage

Production configuration names two T1 Cloud buckets (`config/settings.json`, `core/stores.py`).

#### `fedlab-grade-data` — source, working data, and caches

```text
incoming/<batch>/                         Original write-once Moodle batches
input/<run_id>/                           Canonical copied/unpacked inputs
  Grades-....csv                          Moodle worksheet
  <assignment>/<team_submission>/...      Submitted PDFs and other files
_work/<run_id>/<team_slug>/
  s1_upload.json                          File hash/size and submission metadata
  s2_pages.json                           Extracted/OCR text by slide
  s2_metrics.json                         Fonts, lengths, links, QR codes, readability, etc.
  s2_precheck.json                        PASS/FAIL, reason, and warnings
  s3_detect.json / s3_detect.raw.txt      AI-detection result and raw answer
  s4_grade.json / s4_grade.raw.txt        Validated grade, metadata, and raw answer(s)
  <stage>.manifest.json                   Completion marker and input/config hashes
_cache/llm/<model_id>/<sha256>.json        Exact LLM response cache
_cache/ocr/<file_sha256>/page-<dpi>dpi/
  page_0001.json                          Per-slide OCR response cache
```

This bucket therefore contains sensitive student material as well as derived text and model output. The application cache is shared by runs because identical requests should not be paid for again.

#### `fedlab-result-data` — exports

```text
<run_id>/
  Grades-....csv                          Moodle-ready grades and feedback
  <assignment>/<team_submission>/
    grading_result_feedback.txt           Participant feedback
    grading_result_feedback_long.txt      Staff/jury feedback
  pipeline_results/.../detection.txt      Detection export
  pipeline_results/.../ocr_text.txt       Extracted text export
  grading_results.json                    Aggregate grades
  detection_results.json                  Aggregate detection results
  ocr_results.json                        Aggregate extracted text
  uploaded.json                           Included-work list
  jury_report.csv                         Scores, flags, ties, AI information
  run_report.json                         Configuration summary, cost, counts, ties
  README.md                               Result-format documentation
```

See `docs/STORAGE.md` and `docs/RESULTS.md` for the operator-facing layouts.

### 3.4 Local and ephemeral storage

- Temporary directories hold uploaded/downloaded ZIP files during request processing and ingestion (`api/batch_ops.py:73-104`, `core/stages/ingest.py:134-154`). They are removed afterwards.
- `pg-data`, `redis-data`, and certificate volumes persist service data.
- Tests can use `LocalStorage`, which mirrors the two buckets below a local root. If selected outside tests, the default root is `/data/local-storage` (`core/stores.py:18-27`).
- The production web UI is built into the nginx image; `web/node_modules` and `web/dist` are build artifacts, not application databases.

## 4. How LLM caching saves money

There are **three distinct layers** worth separating: manifest reuse, the application response cache, and provider-side cached prompt tokens. OCR has a fourth, analogous cache.

### 4.1 Layer 0: manifest reuse avoids even entering the LLM stage

Before grading or detection, the pipeline checks the per-team manifest and its input/config hashes. If the unit is already complete, it returns `resumed=True` without calling `Gateway.complete` (`core/stages/grading.py:104-131`, `171-198`).

This is the cheapest path: no provider request and no new `llm_calls` record. It is artifact reuse, not the LLM response cache itself.

If the manifest is missing or invalid but the exact LLM request has been made before, the next layer can still make recomputation free. `tests/test_llm_stages.py:78-84` explicitly covers the lost-manifest case.

### 4.2 Layer 1: exact application-level LLM response cache

**Location in production:**

```text
T1 Cloud bucket: fedlab-grade-data
Prefix:          _cache/llm/<model_id>/<sha256>.json
```

It is **not in Redis or PostgreSQL**. Because it is in shared object storage, it survives process/container restarts and is available to every worker using the same bucket.

#### Cache key

`Gateway.cache_key` serializes this array as JSON and hashes it with SHA-256 (`core/llm.py:94-97`):

```text
[
  provider,
  provider-side model name,
  reasoning_effort,
  temperature,
  full system prompt,
  full user message
]
```

The full user message includes team name, slide text, metrics, and visible pre-check data. Consequently, a prompt edit, model change, reasoning/temperature change, text change, or relevant metrics change produces a different key. Exact whitespace/content matters.

The system prompt is part of the hash even though the numeric prompt version is not separately included. This is correct: content, rather than a version label, determines compatibility.

#### Stored value and lookup flow

The cached JSON contains (`core/llm.py:139-140`):

- `text` — the model answer;
- `prompt_tokens` and `completion_tokens` from the original call;
- `latency_ms` from the original call.

The full request messages are not duplicated in the cache value, although their hash is the object name. Cached answers can still contain student/team information in the generated text.

Flow in `Gateway.complete` (`core/llm.py:104-142`):

1. Compute the deterministic key.
2. Read the S3 object, unless cache use was explicitly bypassed.
3. On a hit, return the stored answer with **0 RUB cost** and record an `llm_calls` row with `cache_hit=True`.
4. On a miss, check the run budget, make the real provider request, store the answer in S3, and record actual usage/cost in PostgreSQL.

The cache lookup occurs before the LLM budget check, so an already-cached answer remains available after a run reaches its cap. That is appropriate because it has no new provider charge. “Zero cost” here refers to LLM/OCR usage; S3, PostgreSQL, and compute infrastructure costs still exist.

A Playground **fresh call** skips the cache read but still writes the new response to the same key after the paid request (`api/routes/llm.py:76-82`, `core/llm.py:104-140`). Thus “fresh” means “do not reuse the old answer,” not “do not cache”; it can replace the previous cached answer.

Errors are not deliberately cached. However, the gateway stores a syntactically invalid or schema-invalid model answer before stage-level validation, so a repeated identical stage request can reuse that answer rather than pay for another attempt.

### 4.3 Layer 2: provider-side prompt/prefix cache billing

This is separate from the application’s S3 cache.

For a real provider call, Yandex may report part of `prompt_tokens` as `prompt_tokens_details.cached_tokens`, typically when a static prompt prefix is reused. The application reads that value and bills those tokens at `price_cached_per_1m`; all other input and output tokens use their normal rates (`core/llm.py:57-61`, `133-141`):

```text
cost = (prompt_tokens - cached_tokens) * input_price / 1,000,000
     + cached_tokens * cached_input_price / 1,000,000
     + completion_tokens * output_price / 1,000,000
```

For the configured Yandex DeepSeek entries, normal input is 300 RUB/1M tokens and cached input is 75 RUB/1M tokens. That is a **75% discount only on the provider-reported cached portion**, not on the whole request and not on output tokens.

The application does not know where Yandex stores this internal cache, does not manage its TTL, and cannot guarantee a provider prefix hit. It simply uses the usage data returned by the provider. Keeping the invariant grading prompt in the system message and variable work data afterwards is compatible with prefix reuse.

Provider cached tokens are recorded in `llm_calls.cached_tokens`. On an application-level S3 hit there is no provider request at all, so cost is zero and no new provider cached-token count is reported.

### 4.4 OCR page cache

OCR is not an LLM call in this implementation, but its cache also saves money.

**Location:**

```text
fedlab-grade-data/_cache/ocr/<pdf_file_sha256>/page-<dpi>dpi/page_NNNN.json
```

The value contains recognized `text`, `model`, and `dpi` (`core/keys.py:53-54`, `core/ocr.py:52-66`). Re-running extraction for the same PDF and slide avoids another OCR charge. Only pages needing OCR are processed, and cached pages are counted as free in the extraction result (`core/stages/extract.py:83-94`).

Unlike the LLM gateway, extraction estimates the cost of **all** candidate pages before consulting the page cache (`core/stages/extract.py:52-60`). Near the budget cap this can reject an operation even if every page would have been a free cache hit.

### 4.5 Cache lifetime and clearing

There is no automatic TTL in application code. Entries persist until an administrator uses the Settings danger zone to clear the LLM and/or OCR prefixes. Clearing caches does not delete stored run results (`api/routes/settings.py:87-122`).

Indefinite retention maximizes reuse but needs a deliberate privacy/storage lifecycle policy. A lifecycle rule can save storage cost but may cause future LLM/OCR charges; those goals should be balanced explicitly.

## 5. Prioritized improvements

### P0 — production safety, correctness, and cost control

#### 5.1 Implement backups and rehearse recovery

**Finding:** the plan calls for scheduled PostgreSQL dumps to S3 and mentions `core.reindex`, but no dump scheduler/script or `core.reindex` implementation is present. `core/db.py:1-5` still describes reindexing as planned.

S3 manifests can help reconstruct run progress, but cannot reconstruct all database-only information: users, API-key hashes, audit history, global settings, model prices, and prompt drafts/versions. PostgreSQL therefore needs its own backup even if S3 is the content source of truth.

**Recommendation:**

- Schedule PostgreSQL custom-format dumps to a separate protected bucket/prefix with encryption, retention, and monitoring.
- Enable appropriate S3 versioning/immutability and lifecycle policies after confirming T1 support.
- Document and automate manifest-based run reconstruction where useful, but do not present it as a substitute for a full database backup.
- Perform a timed restore drill onto an isolated environment and verify login, prompts, runs, artifacts, exports, and billing totals.

#### 5.2 Make the budget a real atomic cap

**Finding:** LLM budget enforcement is `spent >= cap` before a call (`core/llm.py:99-114`). It does not reserve the potential cost of the call, so a call started just below the cap can push spending above it. Concurrent workers can all observe the same old total and start paid calls. OCR has a prospective check, but it is also non-atomic and checks cached pages as if they were paid (`core/pipeline.py:157-179`, `core/stages/extract.py:52-60`).

Usage accounting also has failure windows. The LLM cache PUT precedes the PostgreSQL ledger insert, so an S3 or database failure after a successful provider request can leave its charge unrecorded (`core/llm.py:123-142`). OCR cost is inserted only after all pages and extraction complete; successful pages from a partially failed operation can therefore be paid and cached without a cost row (`core/stages/extract.py:83-94`, `core/pipeline.py:168-179`). Missing LLM usage data currently becomes zero tokens/cost rather than an explicit accounting-unknown state.

**Recommendation:**

- Maintain an atomic per-run spending/reservation ledger, using PostgreSQL row locking or an atomic Redis structure.
- Reserve a conservative maximum before each real request and settle the reservation to actual provider-reported cost afterwards.
- Release reservations on failures and reconcile incomplete reservations after worker crashes.
- Record every external LLM attempt and OCR page as soon as it completes, with cache/request hash and provider request ID where available; reconcile these durable records with cache/artifact writes.
- Treat missing provider usage as unknown/an accounting alert, not as a free call.
- Check the existing OCR cache key before rendering and budget reservation, then render/reserve only for actual misses.
- Add multi-worker and partial-failure tests proving that the cap cannot be exceeded beyond a documented tolerance and that paid work remains accounted for.

#### 5.3 Add atomic stage claims, cache single-flight, and global limits

**Finding:** two workers can miss the same S3 cache key simultaneously and both pay for an identical request. The cache has a check-then-call flow with no single-flight lock. LLM semaphores belong to each `Gateway` and the OCR rate limiter to each OCR object (`core/llm.py:69`, `121-127`; `core/ocr.py:26-48`), so they are not shared across workers; Playground requests also construct separate gateways (`api/routes/llm.py:95-112`). Scaling workers therefore multiplies the effective limits.

The same lack of coordination exists one level higher: a stage checks its manifest and then computes without an execution lease. Standalone-stage and run-all jobs use different arq IDs, so they can execute the same unit concurrently (`api/routes/runs.py:272-301`). Concurrent executions with different settings can interleave fixed-key artifact and manifest writes, leaving a manifest for one configuration beside output from another. `set_item()` also uses read-then-insert/update rather than an atomic upsert or row lock (`core/pipeline.py:50-60`).

**Recommendation:**

- Atomically claim each `(run, team, stage, generation)` using PostgreSQL row locks/`SKIP LOCKED` or an equivalent durable lease with heartbeat and expiry. Use attempt-specific output keys and publish the current manifest only if the generation still matches.
- Add a short-lived distributed lock per LLM/OCR cache key; after acquiring it, re-check S3 before calling the provider.
- Use Redis-backed per-provider/model concurrency and OCR rate limits if more than one worker is supported.
- Ensure lock expiry cannot cause unbounded duplicate calls; include request IDs in usage records for reconciliation.

This is likely the highest-value additional LLM cost saving after the exact cache itself.

#### 5.4 Freeze effective run/stage configuration and make export generation-aware

**Finding:** model and prompt defaults are deliberately excluded from `Run.config` and resolved from current global settings for each team (`core/schemas.py:10-16`, `core/pipeline.py:208-222`). An admin changing settings while a run is active can produce different configurations for different teams in the same logical pass. Results record their own metadata, which helps diagnosis but does not prevent a mixed run.

There is also a potential re-run race: each completed team pipeline independently calls `all_graded` and may enqueue export (`worker/main.py:43-49`). `run_team_pipeline` marks stage 4 queued only when that team reaches it (`core/pipeline.py:282-300`). If old stage-4 rows are already `done`, one fast re-run can observe all teams as finished and export before slower teams have recomputed against the new configuration.

**Recommendation:**

- Snapshot the complete effective configuration at the start of a run or explicit stage generation, including model IDs, prompt versions, reasoning parameters, detection state, and relevant processing settings.
- Require a new generation/revision for changed settings instead of mixing settings within an active pass.
- Use a run-level orchestrator or generation-specific counters to trigger export once, only after every current-generation job has reached a terminal state.
- Make export publication atomic from the reader's perspective: build into a generation-specific prefix and write a current-generation marker last. The current rebuild overwrites result keys and deletes stale files, so a crash or concurrent download can expose a mixed export.
- Add a concurrent re-run/export regression test.

#### 5.5 Bound archive/PDF resource use and stream large responses

**Finding:** the upload cap applies to the compressed ZIP, but ingestion uses `zf.read(name)` for each member without limits on uncompressed size, compression ratio, or member count (`core/stages/ingest.py:134-154`). A small ZIP bomb or a very large uncompressed member can exhaust memory/storage. Whole PDF files are also loaded into memory for extraction.

The export ZIP endpoint builds the entire archive in `BytesIO` and reads every result object into memory (`api/routes/runs.py:243-265`). This is risky on the documented small VM.

**Recommendation:**

- Enforce member-count, per-member uncompressed-size, total-uncompressed-size, and compression-ratio limits before extraction.
- Stream archive members where possible and reject malformed/encrypted archives clearly.
- Add PDF page/file-size limits and timeouts around untrusted parsing/rendering.
- Stream ZIP downloads through a temporary file or streaming zipper; avoid whole-file buffering for PDF and export responses.
- Monitor temporary-disk free space for the 1000 MB upload path.

#### 5.6 Do not rely on anonymization until it is implemented

**Finding:** `RunConfig.anonymize` exists, but it is labelled planned and no stage-2b implementation uses it (`core/schemas.py:10-16`). Setting it through the API currently has no anonymizing effect.

Student source files, extracted text, raw responses, caches, and exports persist in S3; non-anonymized text is sent to external providers.

**Recommendation:**

- Either implement anonymization end-to-end with tests and de-tokenization rules, or remove/reject the inactive option so operators cannot assume protection.
- Document provider data-processing terms and define retention/deletion periods for inputs, work artifacts, raw answers, caches, exports, backups, and logs.
- Treat run deletion as a coordinated lifecycle operation: `core.admin_tools delete-run` currently removes run input/work/export objects and linked database rows, but not shared caches, original `incoming` batches, batch rows, audit events, or already-queued Redis jobs. Define which retention is intentional and prevent jobs from recreating work after deletion.
- Verify bucket encryption and least-privilege credentials; separate routine cache clearing from personal-data erasure requirements.

### P1 — reliability, security, and operability

#### 5.7 Complete cache keys and harden cache behavior

**Findings:**

- The LLM cache key omits `json_mode`/`response_format` and a cache schema version (`core/llm.py:94-105`). A future caller using the same messages but different response settings could incorrectly share an entry.
- OCR keys include file hash, DPI, and page, but not OCR model or language codes (`core/keys.py:53-54`, `core/ocr.py:54-65`). Changing those settings can reuse an incompatible answer.
- Corrupt/old cache JSON is not validated or treated as a miss; decoding/key errors can fail an otherwise recoverable request.
- Fresh calls overwrite the existing cache entry, which may surprise users expecting the old deterministic answer to remain available. Because viewers are intentionally allowed to use Playground, a viewer's fresh experiment—including an invalid answer—can replace a known-good response later reused by pipeline recomputation.
- No output-token ceiling or `finish_reason` check is set/read around the provider call (`core/llm.py:125-137`). A bounded completion budget would limit cost and help detect truncated JSON before it is cached.

**Recommendation:** include every semantics-changing request parameter and a payload schema version in keys; include OCR model/languages and the relevant environment/provider context; validate cached payloads and quarantine/delete invalid entries before retrying as a miss. By default, write fresh answers to a separate attempt/fresh namespace or use put-if-absent rather than replacing a known-good shared entry; make replacement an explicit administrative operation. Add per-stage output limits, include those limits in the key, and reject/handle truncated responses. Avoid semantic/similarity caching for grades—exact caching is safer for correctness.

#### 5.8 Strengthen batch and reingest integrity checks

**Finding:** batch sealing hashes only relative paths and sizes (`core/batches.py:41-62`). Replacing a file with different content of the same size is not detected. Canonical copying also skips a destination solely when its size matches, and the stage-1 manifest identity likewise contains only path and size (`core/stages/ingest.py:104-110`, `189-213`). A same-size corrected file can therefore evade batch-change detection, canonical recopying, and stage-1 invalidation.

A different-name correction has a separate ambiguity: if both old and new PDFs remain in one submission, ingest chooses the lexicographically first PDF rather than an explicitly selected newest/corrected version (`core/stages/ingest.py:189-199`).

**Recommendation:** include a reliable content/version identifier in source metadata and manifests. Prefer a stored SHA-256 for small/manageable inputs; otherwise use S3 ETag/version ID carefully (multipart ETags are not plain MD5). Define correction/version selection explicitly using batch order and source metadata rather than filename sorting. Add same-size/different-content and corrected-filename tests for sealing and late-file ingestion.

#### 5.9 Isolate development from production data

**Finding:** `docs/STORAGE.md:54` states that dev and production currently share both buckets. Different run-ID ranges reduce collisions for run prefixes but do not isolate shared `incoming/` or `_cache/`, and a dev process with production credentials can still modify production data.

**Recommendation:** use separate dev/prod buckets or accounts and least-privilege credentials. At minimum use distinct top-level namespaces and deny dev credentials access to production prefixes. Never use production student data for casual local experiments.

#### 5.10 Resolve the API-key authorization/documentation mismatch

**Finding:** `docs/API.md:7` says admin keys cannot use browser-only Prompts, Models, Settings, or Users functionality. Those routes currently require `Admin`, which accepts admin API keys; only key management uses `SessionAdmin` (`api/routes/llm.py`, `api/routes/settings.py`, `api/routes/users.py`, `api/deps.py:107-114`). `PLAN.md` describes a broader admin-key scope, so the documents themselves are inconsistent.

**Recommendation:** decide the intended least-privilege contract. If sensitive administration must be browser-only, use `SessionAdmin` consistently; otherwise correct `docs/API.md`. Add explicit admin-key tests for every restricted route, not only key management.

#### 5.11 Add production observability and meaningful readiness checks

**Finding:** `/api/health` returns `{ok: true}` without checking dependencies (`api/main.py:55-57`). There is log rotation, but no metrics/exporting, tracing, or alerting implementation was found.

**Recommendation:**

- Separate liveness from readiness; readiness should check PostgreSQL and Redis, with a carefully bounded optional S3 check.
- Add structured logs with run/team/stage/job/request IDs, while redacting student text and secrets.
- Export queue depth/age, job failures/retries, stage durations, S3 latency/errors, LLM tokens/cost, application cache hit rate, provider cached tokens, and budget reservations.
- Alert on failed migrations, backup failure, stuck jobs, repeated provider errors, low disk space, and abnormal spending.

#### 5.12 Test concurrency and migrations on real infrastructure

**Finding:** 80 test functions were found statically across API, pipeline, PDF, cache, export, and batch tests. This is a good base. Tests were not run here. Many tests use SQLite plus `create_all`, which does not validate the actual PostgreSQL migration chain or concurrency behavior (`core/migrate.py:1-5`, `tests/helpers.py`). The web package has build/typecheck but no test script (`web/package.json`).

**Recommendation:**

- Run Alembic upgrade/downgrade/upgrade tests against PostgreSQL and verify ORM metadata matches migrated schema.
- Consider a one-shot migration/seed init job, or conflict-safe seed upserts. Both API and worker currently migrate and then seed; the advisory lock serializes migration, but the subsequent select-then-insert seed operations can still race on a fresh deployment (`api/main.py:32-41`, `worker/main.py:20-26`, `core/registry.py:22-38`).
- Add arq/Redis integration tests for duplicate enqueueing, enqueue failure, worker termination, retry recovery for every entry point, concurrent same-unit execution, multi-worker cache misses, mid-run setting changes, budget races, and export-generation races.
- Add malicious ZIP, huge PDF, corrupt cache, missing-output manifests, detection-input invalidation, cached-token pricing, missing-usage, OCR model/language invalidation, same-size replacement, and partial-failure accounting tests.
- Add frontend component tests and browser E2E coverage for login, upload, role restrictions, run/resume, playground spending, and export.
- Run backend tests, frontend typecheck/build, migration checks, and security scans in CI on every change.

#### 5.13 Make builds reproducible and maintainable

**Finding:** Python requirements use broad lower bounds rather than a lock/constraints file (`requirements.txt`). The nginx web build runs `npm install` despite having a lockfile (`deploy/nginx/Dockerfile:2-7`), and base images are tag- rather than digest-pinned. In addition, `config/.pg_password` is not excluded by `.dockerignore`; selective `COPY` instructions keep it out of the final images, but it is still unnecessarily included in build context sent to the Docker daemon.

**Recommendation:** use reviewed lock/constraints files, `npm ci`, pinned image digests where practical, automated dependency updates, and vulnerability/license scanning. Add `config/.pg_password` to `.dockerignore`. Add Python lint/format/type checks and a documented supported upgrade process.

#### 5.14 Unify retry behavior between “Run all” and individual stages

**Finding:** individual stage jobs use `stage_item_job`, which raises bounded arq `Retry` for transient errors (`worker/main.py:52-62`). “Run all” instead uses `team_pipeline_job` → `run_team_pipeline`; the latter catches stage exceptions, marks the item failed, and returns without raising a worker retry (`worker/main.py:43-49`, `core/pipeline.py:282-300`). Ingest likewise catches every ordinary exception, marks the run failed, and returns normally (`worker/main.py:29-36`). Thus the same transient LLM/S3/OCR/database failure can receive different retry behavior depending on which entry point started it.

**Recommendation:** centralize execution and retry classification so standalone stages, run-all, and ingest use the same bounded policy with jitter. Preserve provider/SDK retries, but define their interaction with worker retries to avoid surprising multiplication. Add identical transient-failure tests for individual-stage, run-all, and ingest paths.

#### 5.15 Hash the actual model input and verify stored artifacts

**Finding:** stage-3 detection builds its request from pages, metrics, and filtered pre-check data, but its manifest input hash contains only pages, team name, and pre-check **status** (`core/stages/grading.py:171-189`). Metrics or visible warnings can change while pages and `PASS` remain unchanged; the old detection manifest then resumes even though the model request would differ. This can retain stale AI/manipulation results.

More generally, `is_done()` checks manifest status and hashes but not whether every declared output still exists or matches its expected content (`core/manifest.py:28-31`). Extraction also trusts the previously recorded `file_sha256` without recomputing the hash of the downloaded bytes (`core/stages/extract.py:27-47`). Missing or replaced objects can therefore undermine resume correctness.

**Recommendation:** hash the exact rendered system/user request plus effective generation parameters, or an equivalent canonical representation with identical semantics. Record required output sizes/checksums in manifests and validate them on resume. Verify source content hashes before extraction, and repair/quarantine incomplete artifact sets instead of trusting the manifest alone.

#### 5.16 Make database state and queue submission atomic

**Finding:** stage rows are committed as `queued` independently of arq enqueue operations, and upload/source changes commit before ingest is enqueued (`api/routes/runs.py:177-190`, `272-303`). Enqueue return values—including arq's duplicate-job `None` result—are ignored. Redis/API failures can leave perpetual queued rows, attached sources without an ingest job, or a misleading `queued: N` response. There is also no lease/reaper for stale queued/running rows after worker loss.

**Recommendation:** add a PostgreSQL transactional outbox and an idempotent dispatcher, or an equivalent coordinated state/job mechanism. Check enqueue results, record delivery failures, and add lease expiration/recovery for abandoned jobs. Test duplicate deduplication and enqueue failure rather than only recording calls in a fake queue.

### P2 — smaller correctness and efficiency improvements

1. **Correct the analytics median.** For an even number of graded works, the code currently selects the upper middle element instead of averaging the two middle values (`api/routes/analytics.py:65`). Add even/odd tests.
2. **Generate frontend API types.** `web/src/types.ts` is manually synchronized and already contains signs of drift, such as a required `GradeArtifact.meta.variant` no longer produced by current grading metadata. Generate types from OpenAPI in CI.
3. **Improve accounting visibility.** Expose `cached_tokens`, billed versus avoided tokens/cost, OCR cache hits, and provider-prefix hit rate in cost endpoints/UI. Currently cached tokens are stored but omitted from the main cost breakdown and team-call response, while OCR hits produce no usage rows (`api/routes/runs.py:111-122`, `168-174`; `core/pipeline.py:173-179`). Distinguish paid calls from error/cache/OCR bookkeeping rows.
4. **Improve cost estimates.** Calibrate the character/token heuristic with recorded usage, include OCR and possible repair retries, and show a range. Preflight exact LLM/OCR keys to show expected hits/misses, but keep provider prefix discounts separate because they are not guaranteed (`api/routes/runs.py:125-154`).
5. **Add relational/index hygiene.** `llm_calls.team_id` is a plain integer without a foreign key or index even though team-filtered queries are common (`core/db.py:175-193`). Add appropriate constraints/indexes after checking deletion semantics.
6. **Batch S3 deletion and pagination.** Cache clearing deletes objects sequentially; use bulk delete/concurrency and bounded batches. Add pagination for growing run/call/audit lists.
7. **Refresh documentation.** Split historical decisions from current architecture. For example, `core/db.py` still says Alembic is future work although startup migrations now exist. Keep a short current-state runbook and an archived decision log.
8. **Track planned product features explicitly.** Admin score overrides, stage-2b anonymization, similarity detection, and score-stability repeats remain planned/open in `PLAN.md`; they should be represented as roadmap items rather than implied existing capabilities.
9. **Share the local object backend explicitly.** If `storage.backend=local` is used with separate API/worker containers, mount the same `/data` volume into both; otherwise each container sees a different filesystem. Alternatively, document and enforce this backend as tests-only (`core/stores.py:18-27`, `docker-compose.yml`).
10. **Reuse the Playground gateway.** Store a shared `Gateway` in application state rather than constructing one per request; this restores provider-client connection reuse and makes the in-process model semaphore useful within the API, while still requiring distributed limits across containers (`api/routes/llm.py:95-112`).
11. **Make prompt kind explicit and enforce it.** New prompt type is currently inferred from whether the name contains `detect`, while Settings checks only for a published name and the frontend filters by name (`api/routes/llm.py:51-58`, `api/routes/settings.py:60-73`). Require `kind` at creation, filter/select by it, and validate that grading/detection defaults use the appropriate kind.
12. **Make UI progress recovery state-driven.** Refetch run state on SSE reconnect and add polling fallback because pub/sub events are ephemeral (`core/events.py:11-17`, `web/src/pages/RunPage.tsx`). Replace fixed export-refresh timers with durable export status/error polling (`web/src/components/ExportCard.tsx`). Also align top-level **Retry failed** selection with the displayed stage-2/stage-4 failures; currently `only_failed=true` selects only failed stage-4 rows (`api/routes/runs.py:290-301`).
13. **Repair the development E2E script.** `scripts/e2e_dev.sh` still sends removed `grade_variants`/`detect_variants` fields and has an unbounded ingest wait without explicit failed-state handling. Update it to the current API and bounded timeout/error reporting so it tests the behavior it claims to test.
14. **Improve audit attribution and startup validation.** Record API-key ID and authentication method in audit details rather than attributing key actions only to the creator (`api/deps.py:65-75`). Enforce minimum session-secret strength at startup, not just non-emptiness (`core/config.py:139-146`).
15. **Require complete extraction before Playground.** Check `s2_pages.json`, `s2_metrics.json`, and `s2_precheck.json`, not only the first; partially written extraction currently risks a server error (`api/routes/llm.py:102-109`).

## 6. Additional cost-saving roadmap

The current design already implements the most important safe savings: code-first pre-checks, optional AI detection, exact persistent response caching, page-level OCR caching, configurable models/prices, and provider cached-token billing. Recommended next steps, in order:

1. **Measure before optimizing:** report paid calls, application cache hits, provider cached tokens, repair calls, OCR pages, actual cost, and estimated avoided cost per run/stage/model.
2. **Prevent duplicate misses:** distributed single-flight is preferable to shortening prompts merely to compensate for accidental duplicate work.
3. **Enforce atomic budgets:** this prevents concurrency and retry behavior from silently defeating the cap.
4. **Keep static prompt content stable and first:** this maximizes both exact-cache and provider-prefix-cache opportunities. Do not inject timestamps or irrelevant changing metadata into prompts.
5. **Use actual tokenization/calibrated estimates:** compare estimate versus actual after each run and include repair/OCR uncertainty. Set safe per-stage output-token ceilings and handle truncation explicitly.
6. **Evaluate providers on quality-adjusted cost:** compare valid-answer rate, repair rate, output tokens, reasoning settings, latency, and price—not just nominal input price.
7. **Retain exact caching for grading:** do not reuse an answer for a merely “similar” student work; that would create correctness and fairness risks.
8. **Set intentional cache retention:** retain high-value deterministic entries, but align indefinite storage with privacy and deletion obligations.

## 7. Suggested implementation sequence

1. Backup/restore and environment isolation.
2. Atomic stage claims, immutable invocation snapshots, and transactional outbox delivery.
3. Unified retries plus generation-safe, atomically published exports.
4. Atomic budget reservations, durable usage reconciliation, and cache single-flight.
5. Exact request/content/OCR hashing and input/output integrity verification.
6. Archive/resource limits and streaming downloads.
7. Privacy/anonymization and retention controls.
8. Observability, CI, dependency locking, and frontend/API contract generation.
9. Smaller analytics, authorization/audit, pagination, UI-recovery, and documentation improvements.

## 8. Overall assessment

The project does not need an architectural rewrite. Its pipeline boundaries, storage abstraction, manifest strategy, scoring controls, and exact persistent cache are appropriate for the workload. The highest-value work is to make the existing design safe under **concurrency, failure recovery, large untrusted files, configuration changes, and real student-data obligations**. Those improvements will also make the cost controls more trustworthy than simply adding further model optimizations.
