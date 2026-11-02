import io
import json

from sqlalchemy import select

from core import keys, pipeline
from core.db import Team
from core.stages.export import fill_worksheet, short_feedback
from core.config import ExportCfg
from tests.fakes import FakeClient, good_detect, good_grade
from tests.test_llm_stages import prepared

CSV = ("Identifier,\"Full name\",Group,Grade,\"Maximum grade\",\"Feedback comments\"\n"
       "P1,A,Альфа,,100,\n P2,B,Альфа,,100,\nP3,C,Другая,,100,\n".replace("\n P2", "\nP2")).encode()


def test_fill_worksheet_keeps_delimiter_and_other_rows():
    out, rep = fill_worksheet(CSV, {"Альфа": (80, "Итоговый балл: 80 из 100.\n\nтекст")})
    text = out.decode()
    assert rep == {"rows": 3, "updated": 2, "delimiter": "comma"}
    assert text.count("80,100") == 2 and "Другая,,100," in text         # untouched row stays untouched
    tabbed = CSV.replace(b",", b"\t")
    assert fill_worksheet(tabbed, {"Альфа": (1, "x")})[1]["delimiter"] == "tab"


def test_short_feedback_matches_cosmos_format_and_dq_text():
    r = good_grade(7)
    r.update(status="EVALUATED", total_score_100=70)
    assert short_feedback(r, ExportCfg()).startswith("Итоговый балл: 70 из 100.\n\nОбратная связь участнику:\n")
    dq = {"status": "DISQUALIFIED", "total_score_100": 0, "disqualification_code": "NOT_PDF", "general_feedback": "x"}
    assert "не в формате PDF" in short_feedback(dq, ExportCfg())


async def test_export_tree_matches_cosmos_layout(tmp_path):
    client = FakeClient([good_detect(0.3), good_grade(8)])
    ctx, run_id, teams = await prepared(tmp_path, client)
    ctx.settings.defaults.detect_prompt = 'ai_detect'
    await ctx.stores.data.put(keys.canonical(run_id, "Grades-test.csv"), CSV)
    async with ctx.sessions() as s:
        from core.db import Run
        run = await s.get(Run, run_id)
        run.source = {"kind": "upload", "worksheet_key": keys.canonical(run_id, "Grades-test.csv")}
        await s.commit()
    for st in ("s3_detect", "s4_grade"):
        for tid in teams.values():
            await pipeline.run_team_stage(ctx, run_id, tid, st)
    summary = await pipeline.run_export_job(ctx, run_id)
    out = ctx.stores.result
    got = sorted(k for k in await out.list(f"{run_id}/"))
    d = f"{run_id}/lab-assign-123/Альфа_101_assignsubmission_file"
    assert f"{d}/grading_result_feedback.txt" in got and f"{d}/grading_result_feedback_long.txt" in got   # no team name in the file names
    assert f"{run_id}/pipeline_results/lab-assign-123/Альфа_101_assignsubmission_file/w/detection.txt" in got
    for name in ("Grades-test.csv", "grading_results.json", "detection_results.json", "ocr_results.json", "uploaded.json",
                 "jury_report.csv", "run_report.json", "README.md"):
        assert f"{run_id}/{name}" in got, name
    assert summary["teams_exported"] == 2 and summary["worksheet"]["updated"] == 2
    csv_text = (await out.get(f"{run_id}/Grades-test.csv")).decode()
    assert "80" in csv_text and "Итоговый балл: 80 из 100." in csv_text
    jury = (await out.get(f"{run_id}/jury_report.csv")).decode("utf-8-sig").strip().splitlines()
    assert len(jury) == 3 and jury[0].startswith("Team,Total/100")                  # header + 2 teams
    report = json.loads(await out.get(f"{run_id}/run_report.json"))
    assert report["cost"]["spent_rub"] > 0 and report["teams_missing_grade"] == []


async def test_csv_both_writes_second_delimiter(tmp_path):
    client = FakeClient([good_grade(8)])
    ctx, run_id, teams = await prepared(tmp_path, client)
    await ctx.stores.data.put(keys.canonical(run_id, "Grades-test.csv"), CSV)
    async with ctx.sessions() as s:
        from core.db import Run
        run = await s.get(Run, run_id)
        run.source = {"kind": "upload", "worksheet_key": keys.canonical(run_id, "Grades-test.csv")}
        await s.commit()
    ctx.settings.export.csv_mode = "both"
    ctx.settings.stages.detect_enabled = False
    await pipeline.run_team_stage(ctx, run_id, teams["Альфа"], "s4_grade")
    summary = await pipeline.run_export_job(ctx, run_id)
    files = await ctx.stores.result.list(f"{run_id}/")
    assert f"{run_id}/Grades-test.csv" in files and f"{run_id}/Grades-test.tab.csv" in files
    assert "\t" in (await ctx.stores.result.get(f"{run_id}/Grades-test.tab.csv")).decode().split("\n")[0]
    assert summary["teams_exported"] == 1


async def test_run_all_pipeline_and_export_trigger(tmp_path):
    client = FakeClient([good_detect(0.4), good_grade(7)])
    ctx, run_id, teams = await prepared(tmp_path, client)
    ctx.settings.defaults.detect_prompt = "ai_detect"
    assert not await pipeline.all_graded(ctx, run_id)
    out = await pipeline.run_team_pipeline(ctx, run_id, teams["Альфа"])
    assert set(out) == {"s2_extract", "s3_detect", "s4_grade"} and out["s2_extract"]["resumed"]   # stage 2 was already done
    await pipeline.run_team_pipeline(ctx, run_id, teams["Docx"])
    assert await pipeline.all_graded(ctx, run_id)
    async with ctx.sessions() as s:
        a, d = await s.get(Team, teams["Альфа"]), await s.get(Team, teams["Docx"])
    assert (a.grade_total, a.ai_probability, d.grade_status) == (70, 0.4, "DISQUALIFIED")


async def test_detect_switch_off_skips_stage_3(tmp_path):
    client = FakeClient([good_grade(7)])
    ctx, run_id, teams = await prepared(tmp_path, client)
    ctx.settings.stages.detect_enabled = False
    out = await pipeline.run_team_pipeline(ctx, run_id, teams["Альфа"])
    assert "s3_detect" not in out and out["s4_grade"]["status"] == "EVALUATED"
    import pytest
    with pytest.raises(ValueError):
        await pipeline.run_team_stage(ctx, run_id, teams["Альфа"], "s3_detect")


async def test_rebuild_removes_files_of_an_older_layout(tmp_path):
    client = FakeClient([good_grade(8)])
    ctx, run_id, teams = await prepared(tmp_path, client)
    ctx.settings.stages.detect_enabled = False
    await pipeline.run_team_stage(ctx, run_id, teams["Альфа"], "s4_grade")
    old = f"{run_id}/lab-assign-123/Альфа_101_assignsubmission_file/w_grading_result_feedback.txt"
    await ctx.stores.result.put(old, b"old naming")
    await pipeline.run_export_job(ctx, run_id)
    files = await ctx.stores.result.list(f"{run_id}/")
    assert old not in files and any(f.endswith("/grading_result_feedback.txt") for f in files)
