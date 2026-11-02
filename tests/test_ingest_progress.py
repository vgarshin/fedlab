"""Stage 1 progress counters (the data behind the ingest progress bar)."""
from core import keys, pipeline
from core.stages import ingest as s1
from tests.conftest import make_pdf, make_zip
from tests.helpers import make_ctx, make_run

TEXT = "Анализ банков для молодёжи и новые продукты. " * 6


def test_progress_percent_weights():
    p = s1.IngestProgress()
    assert p.percent() is None                      # nothing measurable yet -> indeterminate bar
    p.set_bytes_total(1000)
    p.add_total("unpack", 10)
    p.add_total("register", 4)
    p.add_bytes(500)                                 # download half done, unpack/register not started
    assert p.percent() == round((45 * 0.5) / 100 * 100)   # 22: known totals count with 0 progress
    for _ in range(10):
        p.bump("unpack")
    p.add_bytes(500)                                 # download complete, register still pending
    assert p.percent() == round((45 + 40) / 100 * 100)    # 85
    for _ in range(4):
        p.bump("register")
    assert p.percent() == 100
    snap = p.snapshot()
    assert (snap["bytes_done"], snap["bytes_total"], snap["unpack_done"], snap["unpack_total"],
            snap["register_done"], snap["register_total"]) == (1000, 1000, 10, 10, 4, 4)


async def test_ingest_reports_progress_totals(tmp_path):
    ctx = await make_ctx(tmp_path)
    run_id = await make_run(ctx)
    zipped = make_zip({
        "Альфа_101": ("work.pdf", make_pdf([TEXT, TEXT])),
        "Бета_102": ("work.pdf", make_pdf([TEXT])),
    })
    await ctx.stores.data.put(keys.source_zip(run_id), zipped)
    await ctx.stores.data.put(keys.source_file(run_id, "Grades-test.csv"),
                              "Identifier,Group\nP1,Альфа\nP2,Бета\n".encode())
    prog = s1.IngestProgress()
    teams, report = await s1.ingest(ctx.stores.data, run_id, {"kind": "upload"}, progress=prog)
    snap = prog.snapshot()
    assert snap["bytes_total"] == len(zipped) and snap["bytes_done"] == len(zipped)
    assert snap["unpack_total"] == snap["unpack_done"] == 3          # 2 submissions + 1 worksheet csv
    assert snap["register_total"] == snap["register_done"] == len(teams) == report["teams"]
    assert prog.percent() == 100


async def test_run_ingest_publishes_without_redis(tmp_path):
    """The ticker must be skipped (not crash) when there is no redis, as in tests."""
    ctx = await make_ctx(tmp_path, redis=None)
    run_id = await make_run(ctx)
    await ctx.stores.data.put(keys.source_zip(run_id),
                              make_zip({"Альфа_101": ("work.pdf", make_pdf([TEXT]))}))
    report = await pipeline.run_ingest(ctx, run_id)
    assert report["teams"] == 1
