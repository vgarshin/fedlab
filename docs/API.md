# Calling the fedlab API from another service

Base address `https://aigrade.tech/api/…` (JSON). The live, always-current reference is the **API** page of the web app (admin only); this file is the short version.

## Keys
* Create a key on the **API** page (admin, browser login). Choose a name, a role and an expiry. The key `fl_…` is shown **once**; only its hash is stored. Revoke it there at any time — it stops working immediately.
* Roles: **viewer** = read-only (runs, works, results, analytics, downloads); **admin** = also upload, start stages, run all, rebuild export, delete runs, settings-free operations. No key can create/revoke keys or use the browser-only admin pages (Prompts, Models, Settings, Users).
* Send the key as `Authorization: Bearer fl_…` or `X-API-Key: fl_…`. When a key is present, cookies are ignored and the browser CSRF header is not needed.
* `GET /api/auth/me` with the key shows who you are acting as (`"auth": "key"`).

## A typical integration
```bash
export FEDLAB_KEY=fl_...   B=https://aigrade.tech/api
curl -H "Authorization: Bearer $FEDLAB_KEY" -H "Content-Type: application/json" -d '{"name":"Autumn lab"}' $B/runs            # -> {"id": 7, ...}
curl -H "Authorization: Bearer $FEDLAB_KEY" -H "Content-Type: application/json" -d '{"name":"lab-autumn-1"}' $B/batches                    # reserve a NEW batch name (409 if it exists); the reply shows the aws commands
curl -H "Authorization: Bearer $FEDLAB_KEY" -d '{"batch":"lab-autumn-1"}' -H "Content-Type: application/json" $B/runs/7/use-incoming   # after `aws s3 cp` to incoming/lab-autumn-1/ (the batch must be valid and unchanged)
curl -H "Authorization: Bearer $FEDLAB_KEY" $B/runs/7                                                                              # wait for "status": "ready"
curl -X POST -H "Authorization: Bearer $FEDLAB_KEY" -H "Content-Type: application/json" -d '{}' $B/runs/7/run-all               # stages 2-4 + export
curl -H "Authorization: Bearer $FEDLAB_KEY" $B/runs/7                                                                              # poll "stages"
curl -H "Authorization: Bearer $FEDLAB_KEY" -o results.zip $B/runs/7/export/zip
curl -X POST -H "Authorization: Bearer $FEDLAB_KEY" -H "Content-Type: application/json" -d '{"confirm":"DELETE"}' $B/runs/7/delete   # later: remove the run (admin only)
```
`GET /runs/{id}/teams` lists the works with their denormalised state; `review_flags` (`s2:HIDDEN_TEXT`, `s2:INJECTION_SUSPECTED`, `s3:MANIPULATION`) are reviewer hints about manipulation attempts and never affect `grade_total`.

## Deleting a run (admin)
`POST /runs/{id}/delete` with `{"confirm": "DELETE"}` (any other/missing word → `400`). It queues a worker job and answers `{"queued": true}` immediately; the run's `status` becomes `deleting` until the job finishes (then the run is gone). The job deletes in four stages — `input/<id>/` and `_work/<id>/` in the data bucket, `<id>/` in the result bucket, then the database rows — and reports `{type: "delete", stage, done, total}` progress on the run's SSE channel, ending with `{status: "done"}` or `{status: "error"}`. Guards: `409` while the run is ingesting or has queued/running items, while it is already `deleting`, and for a `deleting` run on any other write (stage start, run-all, export, upload, config). Failure marks the run `failed` (note: "deletion failed: …"); calling delete again is safe — finished stages list empty and are skipped. The shared answer cache (`_cache/`), the `incoming/` batches and other runs are never touched.

Live progress over SSE: `GET /runs/{id}/events` (browser only; frames are `data: {json}`). The same channel carries ingest progress and run status events.

Errors: `401` bad/expired/revoked key or not signed in, `403` role too low, `404` unknown id, `409` wrong state (e.g. run-all before ingest finished, or deleting a busy run), `402` budget cap reached, `422` invalid input.
