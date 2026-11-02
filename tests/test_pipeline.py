import json

import pytest
from sqlalchemy import select

from core import keys, pipeline
from core.db import Run, StageItem, Team
from tests.conftest import make_pdf, make_scan_pdf, make_zip
from tests.helpers import make_ctx, make_run

TEXT = "Анализ банков для молодёжи и новые продукты. " * 6


async def setup_run(tmp_path, **cfg):
    ctx = await make_ctx(tmp_path)
    run_id = await make_run(ctx, **cfg)
    zipped = make_zip({
        "Альфа_101": ("work.pdf", make_pdf([TEXT, TEXT])),
        "Scan_102": ("scan.pdf", make_scan_pdf()),
        "Docx_103": ("work.docx", b"PK not a pdf"),
        "._104": ("anon.pdf", make_pdf([TEXT])),
    })
    await ctx.stores.data.put(keys.source_zip(run_id), zipped)
    await ctx.stores.data.put(keys.source_file(run_id, 'Grades-test.csv'),
                              "Identifier,Group\nP1,Альфа\nP2,Альфа\nP3,Other\n".encode())
    return ctx, run_id


async def test_ingest_creates_teams_and_matches_worksheet(tmp_path):
    ctx, run_id = await setup_run(tmp_path)
    report = await pipeline.run_ingest(ctx, run_id)
    assert report["teams"] == 4 and report["worksheet_rows"] == 3
    assert "Scan" in report["unmatched_teams"]
    async with ctx.sessions() as s:
        teams = {t.name: t for t in (await s.execute(select(Team))).scalars()}
    assert teams["Альфа"].csv_matched is True and teams["Scan"].csv_matched is False
    assert teams["Docx"].is_pdf is False


async def test_extract_all_cases(tmp_path):
    ctx, run_id = await setup_run(tmp_path)
    await pipeline.run_ingest(ctx, run_id)
    ids = await pipeline.queue_stage(ctx, run_id, "s2_extract")
    for tid in ids:
        await pipeline.run_team_stage(ctx, run_id, tid, "s2_extract")
    async with ctx.sessions() as s:
        teams = {t.name: t for t in (await s.execute(select(Team))).scalars()}
    assert (teams["Альфа"].precheck_status, teams["Альфа"].slide_count) == ("PASS", 2)
    assert teams["Scan"].precheck_code == "NOT_MACHINE_READABLE"
    assert teams["Docx"].precheck_code == "NOT_PDF"
    assert teams["."].precheck_code == "ANONYMOUS_WORK"


async def test_resume_does_not_recompute(tmp_path):
    ctx, run_id = await setup_run(tmp_path)
    await pipeline.run_ingest(ctx, run_id)
    ids = await pipeline.queue_stage(ctx, run_id, "s2_extract")
    # "crash": only the first two teams finish
    for tid in ids[:2]:
        assert (await pipeline.run_team_stage(ctx, run_id, tid, "s2_extract"))["resumed"] is False
    todo = await pipeline.queue_stage(ctx, run_id, "s2_extract", skip_done=True)
    assert todo == ids[2:]                              # DB says: only unfinished ones
    # even if DB state were lost, S3 manifests make a repeat run a no-op
    for tid in ids[:2]:
        assert (await pipeline.run_team_stage(ctx, run_id, tid, "s2_extract"))["resumed"] is True
    for tid in todo:
        await pipeline.run_team_stage(ctx, run_id, tid, "s2_extract")
    assert await pipeline.queue_stage(ctx, run_id, "s2_extract", skip_done=True) == []


async def test_config_change_invalidates_manifest(tmp_path):
    ctx, run_id = await setup_run(tmp_path)
    await pipeline.run_ingest(ctx, run_id)
    tid = (await pipeline.queue_stage(ctx, run_id, "s2_extract"))[0]
    await pipeline.run_team_stage(ctx, run_id, tid, "s2_extract")
    assert (await pipeline.run_team_stage(ctx, run_id, tid, "s2_extract"))["resumed"] is True
    ctx.settings.processing.char_limit = 5            # different thresholds => different config hash
    assert (await pipeline.run_team_stage(ctx, run_id, tid, "s2_extract"))["resumed"] is False


async def test_ingest_resumes_existing_teams(tmp_path):
    ctx, run_id = await setup_run(tmp_path)
    first = await pipeline.run_ingest(ctx, run_id)
    second = await pipeline.run_ingest(ctx, run_id)
    assert first["resumed"] == 0 and second["resumed"] == 4


async def test_ocr_path_with_fake_ocr(tmp_path):
    ctx, run_id = await setup_run(tmp_path, ocr_enabled=True)

    class FakeOCR:
        calls = 0

        async def page_text(self, sha, number, png):
            FakeOCR.calls += 1
            assert png[:4] == b"\x89PNG"
            return TEXT, False

    ctx._ocr = FakeOCR()
    await pipeline.run_ingest(ctx, run_id)
    async with ctx.sessions() as s:
        scan = (await s.execute(select(Team).where(Team.name == "Scan"))).scalar_one()
    await pipeline.run_team_stage(ctx, run_id, scan.id, "s2_extract")
    assert FakeOCR.calls == 2
    metrics = json.loads(await ctx.stores.data.get(keys.artifact(run_id, scan.slug, "s2_metrics.json")))
    assert metrics["text_source"] == "ocr" and metrics["machine_readable"]


# ---------- ingestion paths and the canonical tree ----------
import zipfile as _zf

from core import keys as _keys
from core.stages import ingest as _ingest


async def _tree(ctx, run_id):
    root = _keys.input_prefix(run_id)
    return sorted(k[len(root):] for k in await ctx.stores.data.list(root) if not k[len(root):].startswith("_"))


EXPECTED = ["Grades-test.csv",
            "lab-assign-123/Docx_103_assignsubmission_file/work.docx",
            "lab-assign-123/._104_assignsubmission_file/anon.pdf",
            "lab-assign-123/Альфа_101_assignsubmission_file/work.pdf",
            "lab-assign-123/Scan_102_assignsubmission_file/scan.pdf"]


async def test_upload_zip_gives_cosmos_tree_with_run(tmp_path):
    ctx, run_id = await setup_run(tmp_path)
    await pipeline.run_ingest(ctx, run_id)
    assert await _tree(ctx, run_id) == sorted(EXPECTED)          # <assignment>/<Team>_<id>_assignsubmission_file/<file>
    async with ctx.sessions() as s:
        t = (await s.execute(select(Team).where(Team.name == "Альфа"))).scalar_one()
    assert t.rel_dir == "lab-assign-123/Альфа_101_assignsubmission_file"
    assert t.file_key == f"input/{run_id}/lab-assign-123/Альфа_101_assignsubmission_file/work.pdf"


async def _incoming_run(tmp_path, as_zip: bool):
    ctx = await make_ctx(tmp_path)
    run_id = await make_run(ctx)
    z = make_zip({"Альфа_101": ("work.pdf", make_pdf([TEXT])), "Scan_102": ("scan.pdf", make_scan_pdf()),
                  "Docx_103": ("work.docx", b"PK x"), "._104": ("anon.pdf", make_pdf([TEXT]))})
    csv = "Identifier,Group\nP1,Альфа\n".encode()
    if as_zip:                                                    # admin copied the zip with `aws s3 cp`
        await ctx.stores.data.put("incoming/b1/all.zip", z)
    else:                                                         # admin unzipped locally (the cosmos way) and copied the folders
        with _zf.ZipFile(__import__("io").BytesIO(z)) as zf:
            for n in zf.namelist():
                if not n.startswith("__MACOSX") and not n.endswith("/"):
                    await ctx.stores.data.put("incoming/b1/" + n, zf.read(n))
    await ctx.stores.data.put("incoming/b1/Grades-test.csv", csv)
    async with ctx.sessions() as s:
        run = await s.get(Run, run_id)
        run.source = {"kind": "incoming", "prefix": "incoming/b1/"}
        await s.commit()
    return ctx, run_id


@pytest.mark.parametrize("as_zip", [True, False])
async def test_incoming_batch_same_tree_as_upload(tmp_path, as_zip):
    ctx, run_id = await _incoming_run(tmp_path, as_zip)
    report = await pipeline.run_ingest(ctx, run_id)
    assert await _tree(ctx, run_id) == sorted(EXPECTED)
    assert report["teams"] == 4 and report["worksheet_rows"] == 1
    assert (await ctx.stores.data.list("incoming/b1/"))                # the source is left untouched


async def test_incoming_ingest_is_idempotent(tmp_path):
    ctx, run_id = await _incoming_run(tmp_path, False)
    a = await pipeline.run_ingest(ctx, run_id)
    b = await pipeline.run_ingest(ctx, run_id)
    assert a["files_copied"] == 5 and b["files_copied"] == 0 and b["files_skipped"] == 5 and b["resumed"] == 4


async def test_two_runs_do_not_collide(tmp_path):
    ctx, r1 = await _incoming_run(tmp_path, True)
    async with ctx.sessions() as s:
        r2 = Run(name="second", config={}, source={"kind": "incoming", "prefix": "incoming/b1/"})
        s.add(r2)
        await s.commit()
        r2_id = r2.id
    await pipeline.run_ingest(ctx, r1)
    await pipeline.run_ingest(ctx, r2_id)
    assert await _tree(ctx, r1) == await _tree(ctx, r2_id) == sorted(EXPECTED)
    assert r1 != r2_id


# ---------- OCR: only the slides without text ----------
from tests.conftest import make_mixed_pdf


class _FakeOCR:
    def __init__(self):
        self.pages = []

    async def page_text(self, sha, number, png):
        self.pages.append(number)
        return "Recognised scanned slide text. " * 3, False


async def _mixed_ctx(tmp_path, **cfg):
    ctx = await make_ctx(tmp_path)
    run_id = await make_run(ctx, **cfg)
    await ctx.stores.data.put(_keys.source_zip(run_id), make_zip({"Mixed_401": ("m.pdf", make_mixed_pdf())}))
    await pipeline.run_ingest(ctx, run_id)
    tid = (await pipeline.queue_stage(ctx, run_id, "s2_extract"))[0]
    return ctx, run_id, tid


async def test_ocr_on_recognises_only_pages_without_text(tmp_path):
    ctx, run_id, tid = await _mixed_ctx(tmp_path, ocr_enabled=True)
    fake = ctx._ocr = _FakeOCR()
    r = await pipeline.run_team_stage(ctx, run_id, tid, "s2_extract")
    assert fake.pages == [2] and r["ocr_pages_paid"] == 1                     # slides 1 and 3 keep their own text
    async with ctx.sessions() as s:
        team = await s.get(Team, tid)
        from core.db import LlmCall
        ocr_rows = (await s.execute(select(LlmCall).where(LlmCall.model_id == "yandex-ocr"))).scalars().all()
    pages = json.loads(await ctx.stores.data.get(_keys.artifact(run_id, team.slug, "s2_pages.json")))
    assert [p["source"] for p in pages] == ["text_layer", "ocr", "text_layer"]
    metrics = json.loads(await ctx.stores.data.get(_keys.artifact(run_id, team.slug, "s2_metrics.json")))
    assert metrics["text_source"] == "mixed" and metrics["ocr_pages"] == [2] and metrics["main_font_size_pt"] == 14
    assert len(ocr_rows) == 1 and ocr_rows[0].cost_rub == pytest.approx(ctx.settings.ocr.price_per_page_rub)   # counted in "spent"
    assert any(w.startswith("OCR_PAGES") for w in json.loads(await ctx.stores.data.get(_keys.artifact(run_id, team.slug, "s2_precheck.json")))["warnings"])


async def test_ocr_off_leaves_scanned_slide_empty(tmp_path):
    ctx, run_id, tid = await _mixed_ctx(tmp_path, ocr_enabled=False)
    await pipeline.run_team_stage(ctx, run_id, tid, "s2_extract")
    async with ctx.sessions() as s:
        team = await s.get(Team, tid)
    assert team.precheck_status == "PASS"
    metrics = json.loads(await ctx.stores.data.get(_keys.artifact(run_id, team.slug, "s2_metrics.json")))
    assert metrics["empty_slides"] == [2] and metrics["ocr_pages"] == []


async def test_ocr_limit_skips_ocr_for_that_work(tmp_path):
    from core.db import AppSetting
    ctx = await make_ctx(tmp_path)
    run_id = await make_run(ctx, ocr_enabled=True)
    ctx._ocr = _FakeOCR()
    async with ctx.sessions() as s:
        s.add(AppSetting(key="ocr.max_pages", value=2))                      # Settings -> OCR -> max pages per work
        await s.commit()
    await ctx.stores.data.put(_keys.source_zip(run_id), make_zip({"Scan_402": ("s.pdf", make_scan_pdf(3))}))   # 3 slides need OCR
    await pipeline.run_ingest(ctx, run_id)
    tid = (await pipeline.queue_stage(ctx, run_id, "s2_extract"))[0]
    await pipeline.run_team_stage(ctx, run_id, tid, "s2_extract")
    async with ctx.sessions() as s:
        scan = await s.get(Team, tid)
    pc = json.loads(await ctx.stores.data.get(_keys.artifact(run_id, scan.slug, "s2_precheck.json")))
    assert ctx._ocr.pages == [] and pc["code"] == "NOT_MACHINE_READABLE"
    assert any(w.startswith("OCR_SKIPPED") for w in pc["warnings"])


async def test_ocr_respects_budget_cap(tmp_path):
    from core.llm import BudgetExceeded
    ctx, run_id, tid = await _mixed_ctx(tmp_path, ocr_enabled=True)
    ctx._ocr = _FakeOCR()
    ctx.settings.budget = {"run_cap_rub": 0.01}                               # one OCR page costs ~0.13 RUB
    with pytest.raises(BudgetExceeded):
        await pipeline.run_team_stage(ctx, run_id, tid, "s2_extract")


def test_review_flags_helpers():
    from core.db import Team
    from core.pipeline import _set_flags, s2_flags
    assert s2_flags(["OVER_CHAR_LIMIT: 1", "HIDDEN_TEXT: x", "PARTIALLY_READABLE: slide(s) [2]",
                     "INJECTION_SUSPECTED: pages [1]"]) == ["HIDDEN_TEXT", "PARTIALLY_READABLE", "INJECTION_SUSPECTED"]
    t = Team(review_flags=["s3:MANIPULATION", "s2:HIDDEN_TEXT"])
    _set_flags(t, "s2", ["INJECTION_SUSPECTED"])
    assert t.review_flags == ["s3:MANIPULATION", "s2:INJECTION_SUSPECTED"]
    _set_flags(t, "s3", [])
    assert t.review_flags == ["s2:INJECTION_SUSPECTED"]
