# Result files (stage 5) — what each file is

Everything is written to `fedlab-result-data/<run>/` and can be rebuilt at any time with **Build export** on the run page.
The layout intentionally repeats the spring (`cosmos`) result bucket, with the run id as the first path component.
**Grades are final integers on a 100-point scale** (six criteria × 1–10, sum of 60 rescaled: `round(sum/60×100)`), computed by code, never by the model.

## Per work (inside the Moodle folder of the work, so the file names carry no team name)
| File | For whom | Content |
|---|---|---|
| `<assignment>/<Team>_<id>_assignsubmission_file/grading_result_feedback.txt` | participants | First line `Итоговый балл: N из 100.` (wording is editable in Settings), then `Обратная связь участнику:` and the general feedback. For a work that failed the technical filter the text says why it was not graded. |
| `…/grading_result_feedback_long.txt` | staff / jury | AI-probability note (informational), flags, every criterion with its score, feedback and evidence (slide numbers), sum, general feedback, technical warnings, which prompt/model/tokens produced it. |
| `pipeline_results/<same path>/<file>/detection.txt` | staff | The AI-detection result (probability, verdict, signals with evidence) or the reason it was skipped. **Informational: never changes a grade.** |
| `pipeline_results/<same path>/<file>/ocr_text.txt` | staff | The text extracted from the PDF, slide by slide (from the text layer, or OCR if it was enabled). This is exactly what the model saw. |

## Aggregates (whole run)
| File | Content |
|---|---|
| `Grades-<assignment>.csv` | The Moodle grading worksheet you uploaded, with **Grade** (0–100) and **Feedback comments** filled for every row whose *Group* equals a graded team. All other columns and rows are unchanged. Delimiter: same as the uploaded file. With Settings → CSV = *both*, a second copy with the other delimiter is written as `Grades-….tab.csv` or `….comma.csv` (the spring pipeline wrote tab-separated). |
| `grading_results.json` | `{ "<assignment>/<Team>_<id>_assignsubmission_file/<file>": { …grade result… } }`. The grade result: `status` (`EVALUATED` / `DISQUALIFIED`), `total_score_100`, `sum_score_60`, `scores.criterion_1…6` (`score` 1–10, `feedback`, `evidence[{slide, description}]`), `general_feedback`, `technical_warnings[]`, `needs_human_review`, `quality_flags[]`, `disqualification_code/reason`. One model and one prompt per run (the defaults on the Settings page; each result records `meta.prompt` and `meta.model`). |
| `detection_results.json` | Same keys → AI-detection result (`ai_probability` 0–1, `verdict`, `confidence`, `signals`, `rationale`) or `null`. |
| `ocr_results.json` | Same keys → the extracted text. |
| `uploaded.json` | List of the works (file paths) included in this export. |
| `jury_report.csv` | One row per work, best first: total, status, six criterion scores, AI probability, **Tie group size**, needs-review flag, warnings, flags. UTF-8 with BOM, so Excel opens Cyrillic correctly. |
| `run_report.json` | Run name, export time, number of works, works not graded yet, worksheet fill statistics, tie groups, works needing review, spent cost, the prompt and model used for grading, whether AI detection was on, and the run options. |
| `README.md` | This document. |

## Terms
* **Ties / Tie group size** – works with the *same* `total_score_100`. "Tie group size = 3" means three evaluated works share that total (1 = unique). The system only marks them; ranking and tie-breaking are done by the jury (calibration session), not by the system.
* **Needs review** – `needs_human_review` set by the model for technical uncertainty (damaged text, contradictory metrics).
* **Manipulation hints** – `HIDDEN_TEXT`, `INJECTION_SUSPECTED` (stage 2, in *Warnings*) and `manipulation_suspected:slides […]` (stage 3, in *Flags*): the work contains text aimed at the grader. Hints for the jury only; they never change a grade and are not in the participant file. See `INJECTIONS.md`.
* **Quality flags** – e.g. `forbidden_words:…` (the feedback still contains a word the lab forbids after one repair attempt), `sum_recomputed:a->b` (the model's arithmetic was wrong and was replaced by ours).
* **Technical filter** – not a PDF / no author or team name / no text layer (and OCR off): total 0, no model call.
