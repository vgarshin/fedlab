# Calling the fedlab API from another service

Base address `https://aigrade.tech/api/…` (JSON). The live, always-current reference is the **API** page of the web app (admin only); this file is the short version.

## Keys
* Create a key on the **API** page (admin, browser login). Choose a name, a role and an expiry. The key `fl_…` is shown **once**; only its hash is stored. Revoke it there at any time — it stops working immediately.
* Roles: **viewer** = read-only (runs, works, results, analytics, downloads); **admin** = also upload, start stages, run all, rebuild export, settings-free operations. No key can create/revoke keys or use the browser-only admin pages (Prompts, Models, Settings, Users).
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
```
`GET /runs/{id}/teams` lists the works with their denormalised state; `review_flags` (`s2:HIDDEN_TEXT`, `s2:INJECTION_SUSPECTED`, `s3:MANIPULATION`) are reviewer hints about manipulation attempts and never affect `grade_total`.

Errors: `401` bad/expired/revoked key or not signed in, `403` role too low, `404` unknown id, `409` wrong state (e.g. run-all before ingest finished), `402` budget cap reached, `422` invalid input.
