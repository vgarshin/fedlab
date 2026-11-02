import json

import pytest
from sqlalchemy import select

from core import keys, pipeline, registry
from core.db import LlmCall, ModelEntry, PromptVersion, Team
from core.llm import BudgetExceeded, Gateway
from core.stages.grading import FORBIDDEN_RE, GradeError, finalize_grade
from tests.conftest import make_pdf, make_zip
from tests.fakes import FakeClient, good_detect, good_grade
from tests.helpers import make_ctx, make_run

TEXT = "Анализ банков для молодёжи и новые продукты. " * 6
MODEL = {"id": "m1", "provider": "yandex", "model": "x", "price_in_per_1m": 300, "price_out_per_1m": 500}


async def prepared(tmp_path, client, **cfg):
    ctx = await make_ctx(tmp_path)
    ctx.settings.models = [MODEL]
    await registry.seed(ctx.sessions, ctx.settings, prompts_dir=tmp_path / "none")
    async with ctx.sessions() as s:
        s.add(PromptVersion(name="grade_short", kind="grade", version=1, text="SYSTEM PROMPT", hash="h1", status="published"))
        s.add(PromptVersion(name="ai_detect", kind="detect", version=1, text="DETECT PROMPT", hash="h2", status="published"))
        await s.commit()
        pid = {p.name: p.id for p in (await s.execute(select(PromptVersion))).scalars()}
    ctx.settings.yandex.folder_id = "folder"
    ctx.settings.defaults.grade_model = ctx.settings.defaults.detect_model = "m1"
    ctx.settings.defaults.grade_prompt = "grade_short"
    ctx._gw = Gateway(ctx.settings, ctx.secrets, ctx.sessions, ctx.stores.data, clients={"yandex": client})
    run_id = await make_run(ctx, **cfg)
    await ctx.stores.data.put(keys.source_zip(run_id), make_zip({
        "Альфа_101": ("w.pdf", make_pdf([TEXT, TEXT])), "Docx_103": ("w.docx", b"PK x")}))
    await pipeline.run_ingest(ctx, run_id)
    for tid in await pipeline.queue_stage(ctx, run_id, "s2_extract"):
        await pipeline.run_team_stage(ctx, run_id, tid, "s2_extract")
    async with ctx.sessions() as s:
        teams = {t.name: t.id for t in (await s.execute(select(Team))).scalars()}
    return ctx, run_id, teams


def test_forbidden_words_regex():
    assert FORBIDDEN_RE.search("В работе есть Ошибка")
    assert not FORBIDDEN_RE.search("Хорошая работа с понятной логикой")


def test_finalize_recomputes_sums():
    r = finalize_grade(good_grade(sum_wrong=True), "T")
    assert (r.sum_score_60, r.total_score_100) == (42, 70) and any(f.startswith("sum_recomputed") for f in r.quality_flags)


def test_finalize_rejects_bad_shapes():
    bad = good_grade(); bad["scores"].pop("criterion_6")
    with pytest.raises(GradeError):
        finalize_grade(bad, "T")
    bad = good_grade(); bad["scores"]["criterion_1"]["score"] = 11
    with pytest.raises(GradeError):
        finalize_grade(bad, "T")


async def test_grade_happy_path_cost_and_cache(tmp_path):
    client = FakeClient([good_grade(8)])
    ctx, run_id, teams = await prepared(tmp_path, client)
    await pipeline.run_team_stage(ctx, run_id, teams["Альфа"], "s4_grade")
    assert len(client.calls) == 1
    assert client.calls[0]["messages"][0]["content"] == "SYSTEM PROMPT"           # prompt is the system message
    assert "<<<НАЧАЛО ТЕКСТА>>>" in client.calls[0]["messages"][1]["content"]
    async with ctx.sessions() as s:
        t = await s.get(Team, teams["Альфа"])
        calls = (await s.execute(select(LlmCall))).scalars().all()
    assert (t.grade_total, t.grade_status) == (80, "EVALUATED")
    assert len(calls) == 1 and calls[0].cost_rub == pytest.approx(1000 * 300 / 1e6 + 200 * 500 / 1e6)
    # second run: manifest => no new call at all
    r = await pipeline.run_team_stage(ctx, run_id, teams["Альфа"], "s4_grade")
    assert r["resumed"] and len(client.calls) == 1


async def test_llm_response_cache_survives_lost_manifest(tmp_path):
    client = FakeClient([good_grade(8)])
    ctx, run_id, teams = await prepared(tmp_path, client)
    await pipeline.run_team_stage(ctx, run_id, teams["Альфа"], "s4_grade")
    await ctx.stores.data.delete(keys.manifest(run_id, "Альфа_101", "s4_grade"))
    await pipeline.run_team_stage(ctx, run_id, teams["Альфа"], "s4_grade")
    assert len(client.calls) == 1                                                   # served from the S3 cache


async def test_repair_on_forbidden_words(tmp_path):
    client = FakeClient([good_grade(6, "Здесь есть ошибка в логике."), good_grade(6, "Логика требует уточнения.")])
    ctx, run_id, teams = await prepared(tmp_path, client)
    await pipeline.run_team_stage(ctx, run_id, teams["Альфа"], "s4_grade")
    assert len(client.calls) == 2 and "запрещённые слова" in client.calls[1]["messages"][1]["content"]


async def test_repair_on_invalid_json_then_fail(tmp_path):
    client = FakeClient(["not json at all", "still { broken"])
    ctx, run_id, teams = await prepared(tmp_path, client)
    with pytest.raises(GradeError):
        await pipeline.run_team_stage(ctx, run_id, teams["Альфа"], "s4_grade")
    assert len(client.calls) == 2


async def test_technical_failure_makes_no_llm_call(tmp_path):
    client = FakeClient([good_grade()])
    ctx, run_id, teams = await prepared(tmp_path, client)
    await pipeline.run_team_stage(ctx, run_id, teams["Docx"], "s4_grade")
    assert client.calls == []
    async with ctx.sessions() as s:
        t = await s.get(Team, teams["Docx"])
    assert (t.grade_total, t.grade_status) == (0, "DISQUALIFIED")


async def test_budget_cap_stops_calls(tmp_path):
    client = FakeClient([good_detect()])
    ctx, run_id, teams = await prepared(tmp_path, client)
    ctx.settings.budget = {"run_cap_rub": 0.0001}                  # global cap from Settings
    await pipeline.run_team_stage(ctx, run_id, teams["Альфа"], "s3_detect")        # first call allowed (nothing spent yet)
    client.replies = [good_grade()]
    with pytest.raises(BudgetExceeded):
        await pipeline.run_team_stage(ctx, run_id, teams["Альфа"], "s4_grade")


async def test_detect_is_stored_and_informational(tmp_path):
    client = FakeClient([good_detect(0.8)])
    ctx, run_id, teams = await prepared(tmp_path, client)
    await pipeline.run_team_stage(ctx, run_id, teams["Альфа"], "s3_detect")
    async with ctx.sessions() as s:
        t = await s.get(Team, teams["Альфа"])
    assert t.ai_probability == 0.8 and t.grade_total is None                        # no effect on grading


def test_evidence_accepts_plain_strings():
    from core.schemas import Evidence
    e = Evidence.model_validate("slide 5: уникальность не раскрыта")
    assert (e.slide, e.description) == (5, "уникальность не раскрыта")
    assert Evidence.model_validate("Слайд 12 — мало текста").slide == 12
    assert Evidence.model_validate("без номера").slide is None
    assert Evidence.model_validate({"slide": 3, "description": "x"}).slide == 3


def test_partially_readable_never_reaches_the_grader():
    """The flag is for reviewers only: the prompt must stay byte-identical so the LLM answer
    cache still hits (re-grades stay free and scores cannot shift because of the flag)."""
    from core.stages.grading import build_user_message
    pages = [{"number": 1, "text": "текст", "chars": 5, "source": "text_layer", "has_images": False, "empty": False, "garbled": False}]
    metrics = {"slide_count": 2, "chars_with_spaces": 5, "no_text_slides": [2],
               "hidden_text_pages": [], "injection_hits": [], "ocr_pages": []}
    pc = {"status": "PASS", "code": None, "reason": None,
          "warnings": ["PARTIALLY_READABLE: slide(s) [2] have no usable text layer; no text for [2]"]}
    msg = build_user_message("Team A", pages, metrics, pc)
    assert "PARTIALLY_READABLE" not in msg and "no_text_slides" not in msg
