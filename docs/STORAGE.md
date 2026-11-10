# Storage layout (T1 Cloud buckets) — same tree as `cosmos`, with the run kept

## Batches: how a Moodle export gets into the system
Every source is a **batch**: a folder `incoming/<name>/` in the data bucket that is created **once** and **never extended**.
* **Name**: you choose it; the default is `YYYYMMDD-HHMM` (e.g. `20261028-1415`, no colon: awkward in file names and URLs). Rules: 3-60 characters, letters / digits / `.` `-` `_`, starting with a letter or digit.
* **Contents**: either exactly one zip, or the unzipped Moodle folders; plus at most one worksheet CSV.
* **Ways in** (Run page → *Source files*):
  1. **Upload from this computer** – name + zip (+ CSV); the app creates the batch and uses it for the run. An existing name is refused. The zip is limited to **1000 MB** (`processing.max_upload_mb` in `config/settings.json`; the limit is shown on the page and checked before the upload starts). For bigger exports: split the export into parts, or use the AWS CLI way below.
  2. **Copy with the AWS CLI** – *Reserve this name* in the app, then run the shown `aws s3 cp …` commands once.
  3. **Existing batches** – pick a batch that is valid and unchanged.
* **Guards**: the app never writes into an existing batch (reserve and upload refuse an existing name). The AWS CLI cannot be technically blocked, so such abuse is **detected**: a batch with 2 zips, 2 CSVs, or zip + unzipped works is marked *invalid*; a batch whose files changed after a run first used it is marked *changed after use*; invalid or changed batches cannot be used. Batches found in the bucket that were never reserved are shown as *outside the app* (usable if valid). A batch that a run used cannot be deleted; unused ones can.
* **Late or corrected files**: make a **new** batch and add it to the same run (a run can have several batches; works already stored are skipped, a changed file replaces the stored one, the newest worksheet wins).
* The batch folder is never modified by the app: the run gets its own copy under `input/<run>/`.

```
aws s3 cp ./moodle_export.zip s3://fedlab-grade-data/incoming/<batch>/ --endpoint-url https://api.s3.dp.t1.cloud --region dc1
aws s3 cp ./Grades-worksheet.csv s3://fedlab-grade-data/incoming/<batch>/ --endpoint-url https://api.s3.dp.t1.cloud --region dc1
```

## Canonical input tree (identical to cosmos' check bucket, plus the run)
```
fedlab-grade-data/
├─ incoming/<batch>/...                          what the admin copied (untouched)
├─ input/<run>/
│   ├─ Grades-<assignment>.csv                   Moodle grading worksheet (original name)
│   ├─ <assignment-dir>/                         e.g. lab_autumn_2026-Загрузить решение кейса-12791
│   │   └─ <Team>_<submissionId>_assignsubmission_file/<file>.pdf
│   └─ _source/submissions.zip                   only for UI uploads
├─ _work/<run>/<Team>_<submissionId>/            every intermediate result, per team
│   ├─ s1_upload.json  s2_pages.json  s2_metrics.json  s2_precheck.json
│   ├─ s3_detect.json (+ .raw.txt)  s4_grade.json (+ .raw.txt)    each records the prompt version and model used
│   └─ <stage>.manifest.json                     written LAST: "this unit is complete"
└─ _cache/llm/<model_id>/<sha256>.json , _cache/ocr/<file_sha256>/page-200dpi/page_0001.json
```

## Result tree (stage 5) — cosmos layout, `<run>` kept
```
fedlab-result-data/<run>/
├─ Grades-<assignment>.csv                       worksheet with Grade + Feedback comments filled (original delimiter kept)
├─ <assignment-dir>/<Team>_<id>_assignsubmission_file/
│   ├─ grading_result_feedback.txt                participant text:  "Итоговый балл: N из 100." + feedback
│   └─ grading_result_feedback_long.txt           staff text: AI note, every criterion with evidence, warnings, flags
├─ pipeline_results/<assignment-dir>/<Team>_<id>_assignsubmission_file/<file>/{detection.txt, ocr_text.txt}
├─ grading_results.json  detection_results.json  ocr_results.json  uploaded.json     (same names as cosmos)
├─ jury_report.csv                               per-criterion scores, AI probability, ties
└─ run_report.json                               config snapshot, prompt versions, cost, ties, needs-review list
```
The export is a pure function of stored artifacts: *Rebuild export* can be pressed at any time.

## Differences from cosmos worth knowing
* A `<run>` prefix in every key: runs never overwrite each other (cosmos had one flat tree).
* All intermediate results are stored per team with manifests (cosmos kept them in memory; a failure meant a restart).
* Creating a run refuses to start if its prefix already holds objects (protects against run-id reuse after a database reset).
* Dev and production currently share the same two buckets: use the `local` storage backend or distinct prefixes for experiments that must not touch production data.

## Deleting a run (Run page → Danger zone, admin)
`POST /api/runs/{id}/delete` with the typed confirmation `DELETE` queues a worker job that removes exactly the run's own data, stage by stage (each stage reported as live progress on the run page):
1. `input/<run>/` in the data bucket — the Moodle sources of this run.
2. `_work/<run>/` in the data bucket — intermediate artifacts and manifests.
3. `<run>/` in the result bucket — exported results.
4. Database rows — `llm_calls`, `stage_items`, `teams`, then the `runs` row (after it, the run disappears from the Runs list).

**Never touched**: the shared answer cache `_cache/llm/` and `_cache/ocr/` (paid answers, reused by all runs), the `incoming/<batch>/` source batches (a batch used by another run must survive), and every other run's data. The deletion is audit-logged (`run_delete_start` / `run_delete_done`) and idempotent: if it fails midway (the run is marked `failed` with the reason), starting it again skips the already-empty prefixes. While the run is `deleting`, no new work can be queued on it, so no stage can recreate files after their prefix was wiped.
