"""Stage 5: build the result tree (same layout as the spring `cosmos` result bucket, with the run kept).

    <run>/<Grades-....csv>                                   Moodle worksheet with Grade + Feedback comments filled
    <run>/<assignment>/<Team>_<id>_assignsubmission_file/<stem>_grading_result_feedback.txt        (participant text)
    <run>/<assignment>/<Team>_<id>_assignsubmission_file/<stem>_grading_result_feedback_long.txt   (staff text)
    <run>/pipeline_results/<same dir>/<stem>/detection.txt, ocr_text.txt
    <run>/grading_results.json  detection_results.json  ocr_results.json  uploaded.json
    <run>/jury_report.csv       scores per criterion, AI probability, ties, flags
    <run>/run_report.json       model and prompt used, cost, counts, ties

Pure function of stored stage 2-4 artifacts + the worksheet: safe to re-run at any time. Admin overrides
(planned) will be applied here.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

from core import keys, moodle
from core.config import ExportCfg
from core.schemas import CRITERIA, RunConfig
from core.storage import Storage, get_json, put_json

DQ_TEXT = {
    "NOT_PDF": "работа представлена не в формате PDF",
    "ANONYMOUS_WORK": "в работе не указано название команды или автор",
    "NOT_MACHINE_READABLE": "PDF-файл не является машиночитаемым (в нём нет текстового слоя)",
}


@dataclass
class TeamRow:
    name: str
    slug: str
    rel_dir: str
    filename: str
    submission_id: str


def short_feedback(result: dict, cfg: ExportCfg) -> str:
    """Participant-facing text: the score line + the model's general feedback (cosmos format)."""
    total = result["total_score_100"]
    body = result.get("general_feedback", "")
    if result["status"] == "DISQUALIFIED":
        reason = DQ_TEXT.get(result.get("disqualification_code") or "", result.get("disqualification_reason") or "")
        body = f"Работа не оценивалась по существу: {reason}." if reason else body
    return f"{cfg.score_line.format(total=total)}\n\n{cfg.feedback_header}\n{body}\n"


def long_feedback(result: dict, detect: dict | None, meta: dict) -> str:
    """Staff text: AI note first, then every criterion with score, feedback and evidence (cosmos 'long' file)."""
    lines: list[str] = []
    d = (detect or {}).get("result")
    if d:
        lines.append(f"Вероятность ИИ-генерации (справочно, на оценку не влияет): {round(d['ai_probability'] * 100)}% "
                     f"({d['verdict']}, уверенность {d['confidence']}). {d['rationale']}")
    if result.get("needs_human_review"):
        lines.append("ТРЕБУЕТСЯ РУЧНАЯ ПРОВЕРКА (техническая неопределённость).")
    for f in result.get("quality_flags", []):
        lines.append(f"Флаг качества: {f}")
    lines.append("")
    if result["status"] == "DISQUALIFIED":
        lines += [f"Работа снята с оценивания: {result.get('disqualification_code')}",
                  result.get("disqualification_reason") or ""]
    else:
        for k, c in result["scores"].items():
            lines.append(f"{c['name']} ({c['score']})")
            lines.append(c["feedback"])
            lines += [f"  — {'[слайд ' + str(e['slide']) + '] ' if e.get('slide') is not None else ''}{e['description']}"
                      for e in c.get("evidence", [])]
            lines.append("")
        lines.append(f"Сумма: {result['sum_score_60']} из 60; итог: {result['total_score_100']} из 100.")
    lines += ["", "Общая обратная связь:", result.get("general_feedback", "")]
    if result.get("technical_warnings"):
        lines += ["", "Технические замечания:"] + [f"  — {w}" for w in result["technical_warnings"]]
    lines += ["", f"[{meta.get('prompt')} · {meta.get('model')} · {meta.get('prompt_tokens')}/{meta.get('completion_tokens')} tokens]"]
    return "\n".join(lines) + "\n"


def fill_worksheet(raw: bytes, grades: dict[str, tuple[int, str]], delimiter: str | None = None) -> tuple[bytes, dict]:
    """Fill Grade and 'Feedback comments' for every worksheet row whose Group is a graded team.
    Keeps the file's BOM and column order; the delimiter is the file's own unless ``delimiter`` is given."""
    text = raw.decode("utf-8-sig", errors="replace")
    bom = raw.startswith(b"\xef\xbb\xbf")
    first = text.split("\n", 1)[0]
    src_delim = "\t" if first.count("\t") > first.count(",") else ","
    delim = delimiter or src_delim
    reader = csv.DictReader(io.StringIO(text), delimiter=src_delim)
    fields = reader.fieldnames or []
    rows = list(reader)
    by_group = {moodle.norm_team(g): v for g, v in grades.items()}
    updated = 0
    for r in rows:
        hit = by_group.get(moodle.norm_team(r.get("Group", "")))
        if hit:
            r["Grade"], r["Feedback comments"] = str(hit[0]), hit[1]
            updated += 1
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=fields, delimiter=delim, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    data = out.getvalue().encode("utf-8")
    return (b"\xef\xbb\xbf" + data if bom else data), {"rows": len(rows), "updated": updated, "delimiter": "tab" if delim == "\t" else "comma"}


class _Recorder:
    """Wraps the result storage and remembers every key written, so stale files of an older export can be removed."""

    def __init__(self, st: Storage):
        self._st, self.written, self.bucket = st, set(), st.bucket

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.written.add(key)
        await self._st.put(key, data, content_type)


async def run_export(*, data: Storage, result: Storage, run_id: int, run_name: str, cfg: RunConfig, teams: list[TeamRow],
                     worksheet_key: str | None, export_cfg: ExportCfg, cost: dict, detect_enabled: bool = True,
                     readme: str = "") -> dict:
    prefix = keys.result_prefix(run_id)
    previous = set(await result.list(prefix))
    result = _Recorder(result)  # type: ignore[assignment]
    grading, detection, ocr, uploaded, jury, missing = {}, {}, {}, [], [], []
    grades_for_csv: dict[str, tuple[int, str]] = {}

    sem = asyncio.Semaphore(8)             # many small S3 objects per team: overlap the round trips

    async def one(t: TeamRow) -> dict | None:
        async with sem:
            primary = await get_json(data, keys.artifact(run_id, t.slug, "s4_grade.json"))
            if primary is None:
                return {"missing": t.name}
            det = await get_json(data, keys.artifact(run_id, t.slug, "s3_detect.json")) if detect_enabled else None
            pages = await get_json(data, keys.artifact(run_id, t.slug, "s2_pages.json")) or []
            stem = t.filename.rsplit(".", 1)[0]
            res, meta = primary["result"], primary["meta"]
            short, long_ = short_feedback(res, export_cfg), long_feedback(res, det, meta)
            base = f"{prefix}{t.rel_dir}/"          # the folder already carries the team name: no name in the file names
            side = f"{prefix}pipeline_results/{t.rel_dir}/{stem}/"
            text = "\n\n".join(f"[Слайд {p['number']}]\n{p['text']}" for p in pages)
            d = (det or {}).get("result")
            manip = (d or {}).get("manipulation_attempt") or {}
            if manip.get("found"):      # stage 3 semantic check -> reviewer flag; scores untouched
                res = {**res, "quality_flags": [*res.get("quality_flags", []), f"manipulation_suspected:slides {manip.get('slides', [])}"]}
                short, long_ = short_feedback(res, export_cfg), long_feedback(res, det, meta)
            await asyncio.gather(
                result.put(f"{base}grading_result_feedback.txt", short.encode()),
                result.put(f"{base}grading_result_feedback_long.txt", long_.encode()),
                result.put(side + "ocr_text.txt", text.encode()),
                result.put(side + "detection.txt", (json.dumps(d, ensure_ascii=False, indent=2) if d else
                                                    f"AI detection: {(det or {}).get('reason') or 'not run'}").encode()))
            return {"rel_file": f"{t.rel_dir}/{t.filename}", "res": res, "text": text, "det": d, "short": short, "name": t.name,
                    "jury": {"team": t.name, "total": res["total_score_100"], "status": res["status"], "res": res,
                             "ai": (d or {}).get("ai_probability"), "review": res["needs_human_review"],
                             "model": f"{meta.get('prompt')} × {meta.get('model')}"}}

    for r in await asyncio.gather(*(one(t) for t in sorted(teams, key=lambda x: x.name.casefold()))):
        if "missing" in r:
            missing.append(r["missing"])
            continue
        grading[r["rel_file"]], ocr[r["rel_file"]], detection[r["rel_file"]] = r["res"], r["text"], r["det"]
        uploaded.append(r["rel_file"])
        grades_for_csv[r["name"]] = (r["res"]["total_score_100"], r["short"].strip())
        jury.append(r["jury"])

    ties = {total: n for total, n in Counter(j["total"] for j in jury if j["status"] == "EVALUATED").items() if n > 1}
    csv_report = None
    if worksheet_key and (raw := await data.get(worksheet_key)) is not None:
        name = worksheet_key.rsplit("/", 1)[-1]
        filled, csv_report = fill_worksheet(raw, grades_for_csv)                    # same delimiter as uploaded
        mode = export_cfg.csv_mode
        main_delim = csv_report["delimiter"]
        if mode in ("comma", "tab") and mode != main_delim:                         # forced delimiter replaces the main file
            filled, csv_report = fill_worksheet(raw, grades_for_csv, "," if mode == "comma" else "\t")
        await result.put(f"{prefix}{name}", filled, "text/csv")
        if mode == "both":                                                          # plus the other delimiter as a second file
            other = "tab" if main_delim == "comma" else "comma"
            alt, _ = fill_worksheet(raw, grades_for_csv, "\t" if other == "tab" else ",")
            await result.put(f"{prefix}{name.rsplit('.', 1)[0]}.{other}.csv", alt, "text/csv")
            csv_report["extra_file"] = f"{name.rsplit('.', 1)[0]}.{other}.csv"
    for name, obj in (("grading_results.json", grading), ("detection_results.json", detection),
                      ("ocr_results.json", ocr), ("uploaded.json", uploaded)):
        await put_json(result, prefix + name, obj)
    await result.put(prefix + "jury_report.csv", _jury_csv(jury, ties), "text/csv")
    if readme:
        await result.put(prefix + "README.md", readme.encode(), "text/markdown")
    report = {"run_id": run_id, "run_name": run_name, "exported_at": datetime.now(timezone.utc).isoformat(),
              "teams_exported": len(jury), "teams_missing_grade": missing, "worksheet": csv_report,
              "tie_groups": {str(k): v for k, v in sorted(ties.items(), reverse=True)},
              "needs_review": [j["team"] for j in jury if j["review"]], "cost": cost,
              "grading_used": sorted({j["model"] for j in jury}), "ai_detection_enabled": detect_enabled,
              "run_config": cfg.model_dump()}
    await put_json(result, prefix + "run_report.json", report)
    for stale in previous - result.written:          # files of an older export that this one no longer produces (e.g. renamed)
        await result._st.delete(stale)
    return {k: report[k] for k in ("exported_at", "teams_exported", "teams_missing_grade", "worksheet", "tie_groups", "needs_review")}


JURY_HEAD = ["Team", "Total/100", "Status", *CRITERIA.values(), "AI prob. (info)", "Tie group size", "Needs review", "Warnings", "Flags"]


def _jury_rows(jury: list[dict], ties: dict[int, int]) -> list[list]:
    out = []
    for j in sorted(jury, key=lambda x: (-x["total"], x["team"].casefold())):
        r = j["res"]
        crit = [r["scores"][k]["score"] if r["scores"] else None for k in CRITERIA]
        out.append([j["team"], j["total"], r["status"], *crit, j["ai"], ties.get(j["total"], 1) if r["status"] == "EVALUATED" else "",
                    "YES" if j["review"] else "", "; ".join(r["technical_warnings"]), "; ".join(r["quality_flags"])])
    return out


def _jury_csv(jury: list[dict], ties: dict[int, int]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(JURY_HEAD)
    w.writerows(_jury_rows(jury, ties))
    return b"\xef\xbb\xbf" + buf.getvalue().encode()      # BOM so Excel opens Cyrillic correctly
