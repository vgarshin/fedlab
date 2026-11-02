import io
import zipfile

import httpx
import pytest
from httpx import ASGITransport

from api.deps import AppState
from api.main import create_app
from core import batches
from tests.conftest import make_pdf, make_zip
from tests.helpers import make_ctx
from tests.test_api import FakeQueue, H, login, seeded_client

TEXT = "Анализ банков для молодёжи и новые продукты. " * 6
CSV = "Identifier,Group\nP1,Альфа\n".encode()


def zip_bytes(name="Альфа_101") -> bytes:
    return make_zip({name: ("w.pdf", make_pdf([TEXT]))})


async def run_id(c) -> int:
    return (await c.post("/api/runs", json={"name": "r"}, headers=H)).json()["id"]


def test_default_name_has_no_colon():
    from datetime import datetime
    assert batches.default_name(datetime(2026, 10, 28, 14, 5)) == "20261028-1405"
    assert batches.NAME_RE.match("20261028-1405") and not batches.NAME_RE.match("20261028-14:05") and not batches.NAME_RE.match("a b")


async def test_reserve_refuses_existing_name_and_gives_commands(tmp_path):
    c, _ = await seeded_client(tmp_path)
    async with c:
        await login(c)
        r = await c.post("/api/batches", json={"name": "lab-autumn-1"}, headers=H)
        assert r.status_code == 201 and "s3://" in r.json()["commands"][0] and "lab-autumn-1" in r.json()["commands"][0]
        again = await c.post("/api/batches", json={"name": "lab-autumn-1"}, headers=H)
        assert again.status_code == 409 and "never added to an existing batch" in again.json()["detail"]
        assert (await c.post("/api/batches", json={"name": "bad name!"}, headers=H)).status_code == 422
        auto = (await c.post("/api/batches", json={}, headers=H)).json()["name"]
        assert len(auto) == 13 and auto[8] == "-"                                        # YYYYMMDD-HHMM
        auto2 = (await c.post("/api/batches", json={}, headers=H)).json()["name"]
        assert auto2 != auto and auto2.startswith(auto)                                  # same minute => -2


async def test_ui_upload_creates_batch_once(tmp_path):
    c, q = await seeded_client(tmp_path)
    async with c:
        await login(c)
        rid = await run_id(c)
        up = await c.post(f"/api/runs/{rid}/upload", data={"batch_name": "first-drop"},
                          files={"submissions": ("export.zip", zip_bytes()), "worksheet": ("Grades-x.csv", CSV)}, headers=H)
        assert up.status_code == 200 and up.json()["batch"] == "first-drop" and q.jobs[-1][0] == "ingest_run_job"
        st = c._transport.app.state.fedlab
        keys_ = sorted(k for k in await st.stores.data.list("incoming/first-drop/"))
        assert keys_ == ["incoming/first-drop/Grades-x.csv", "incoming/first-drop/export.zip"]
        # a second upload under the SAME name is refused and nothing is added
        rid2 = await run_id(c)
        dup = await c.post(f"/api/runs/{rid2}/upload", data={"batch_name": "first-drop"}, files={"submissions": ("more.zip", zip_bytes("Бета_102"))}, headers=H)
        assert dup.status_code == 409 and "already exists" in dup.json()["detail"]
        assert sorted(await st.stores.data.list("incoming/first-drop/")) == keys_
        # without a name: default YYYYMMDD-HHMM
        auto = await c.post(f"/api/runs/{rid2}/upload", files={"submissions": ("e.zip", zip_bytes("Бета_102"))}, headers=H)
        assert auto.status_code == 200 and len(auto.json()["batch"]) >= 13
        # not a zip / no submissions: rejected and no batch left behind
        bad = await c.post(f"/api/runs/{rid2}/upload", data={"batch_name": "broken"}, files={"submissions": ("e.zip", b"nope")}, headers=H)
        empty = await c.post(f"/api/runs/{rid2}/upload", data={"batch_name": "empty-zip"}, files={"submissions": ("e.zip", make_zip({}))}, headers=H)
        assert bad.status_code == 400 and empty.status_code == 400
        names = {b["name"] for b in (await c.get("/api/batches")).json()["batches"]}
        assert "broken" not in names and "empty-zip" not in names


async def test_cli_batch_validation_and_cosmos_demo_case(tmp_path):
    """The reported problem: files added to an existing batch (2 zips + 2 CSVs) => the batch is flagged and cannot be used."""
    c, _ = await seeded_client(tmp_path)
    async with c:
        await login(c)
        st = c._transport.app.state.fedlab
        rid = await run_id(c)
        d = st.stores.data
        await d.put("incoming/demo-zip/old.zip", zip_bytes())
        await d.put("incoming/demo-zip/Grades-old.csv", CSV)
        listed = {b["name"]: b for b in (await c.get("/api/batches")).json()["batches"]}
        assert listed["demo-zip"]["usable"] and listed["demo-zip"]["status"] == "unregistered" and listed["demo-zip"]["origin"] == "outside"
        await d.put("incoming/demo-zip/new.zip", zip_bytes("Бета_102"))            # someone adds files to the existing batch
        await d.put("incoming/demo-zip/Grades-new.csv", CSV)
        b = {x["name"]: x for x in (await c.get("/api/batches")).json()["batches"]}["demo-zip"]
        assert not b["usable"] and any("2 zip files" in p for p in b["problems"]) and any("2 CSV files" in p for p in b["problems"])
        r = await c.post(f"/api/runs/{rid}/use-incoming", json={"batch": "demo-zip"}, headers=H)
        assert r.status_code == 409 and "cannot be used" in r.json()["detail"]
        # mixing a zip with unzipped works is also invalid
        await d.put("incoming/mixed/a.zip", zip_bytes())
        await d.put("incoming/mixed/Т_1_assignsubmission_file/x.pdf", make_pdf([TEXT]))
        m = {x["name"]: x for x in (await c.get("/api/batches")).json()["batches"]}["mixed"]
        assert any("both a zip and unzipped" in p for p in m["problems"])
        # empty reserved batch cannot be used
        await c.post("/api/batches", json={"name": "empty-one"}, headers=H)
        assert (await c.post(f"/api/runs/{rid}/use-incoming", json={"batch": "empty-one"}, headers=H)).status_code == 409


async def test_batch_is_sealed_on_use_and_changes_are_detected(tmp_path):
    c, q = await seeded_client(tmp_path)
    async with c:
        await login(c)
        st = c._transport.app.state.fedlab
        rid = await run_id(c)
        await c.post("/api/batches", json={"name": "drop-1"}, headers=H)
        await st.stores.data.put("incoming/drop-1/all.zip", zip_bytes())                  # the CLI upload
        ok = await c.post(f"/api/runs/{rid}/use-incoming", json={"batch": "drop-1"}, headers=H)
        assert ok.status_code == 200 and ok.json()["batches"] == ["drop-1"]
        b = {x["name"]: x for x in (await c.get("/api/batches")).json()["batches"]}["drop-1"]
        assert b["status"] == "used" and b["used_by"] == [rid] and b["usable"]
        # reuse by a second run is fine while nothing changed
        rid2 = await run_id(c)
        assert (await c.post(f"/api/runs/{rid2}/use-incoming", json={"batch": "drop-1"}, headers=H)).status_code == 200
        # late file added to the existing batch => flagged, refused
        await st.stores.data.put("incoming/drop-1/late.zip", zip_bytes("Бета_102"))
        b = {x["name"]: x for x in (await c.get("/api/batches")).json()["batches"]}["drop-1"]
        assert b["modified_after_use"] and not b["usable"]
        rid3 = await run_id(c)
        assert (await c.post(f"/api/runs/{rid3}/use-incoming", json={"batch": "drop-1"}, headers=H)).status_code == 409


async def test_delete_only_unused_batches(tmp_path):
    c, _ = await seeded_client(tmp_path)
    async with c:
        await login(c)
        st = c._transport.app.state.fedlab
        rid = await run_id(c)
        await st.stores.data.put("incoming/junk/a.zip", zip_bytes())
        await st.stores.data.put("incoming/keep/a.zip", zip_bytes())
        await c.post(f"/api/runs/{rid}/use-incoming", json={"batch": "keep"}, headers=H)
        assert (await c.delete("/api/batches/keep", headers=H)).status_code == 409
        r = await c.delete("/api/batches/junk", headers=H)
        assert r.status_code == 200 and r.json()["deleted_objects"] == 1 and not await st.stores.data.list("incoming/junk/")


async def test_run_can_take_a_second_batch_for_late_files(tmp_path):
    from sqlalchemy import select
    from core import pipeline
    from core.db import Team
    c, _ = await seeded_client(tmp_path)
    async with c:
        await login(c)
        st = c._transport.app.state.fedlab
        rid = await run_id(c)
        await st.stores.data.put("incoming/b-1/e.zip", zip_bytes("Альфа_101"))
        await st.stores.data.put("incoming/b-2/e.zip", zip_bytes("Бета_102"))
        await c.post(f"/api/runs/{rid}/use-incoming", json={"batch": "b-1"}, headers=H)
        await c.post(f"/api/runs/{rid}/use-incoming", json={"batch": "b-2"}, headers=H)
        run = (await c.get(f"/api/runs/{rid}")).json()
        assert run["source"]["batches"] == ["b-1", "b-2"]
        ctx = pipeline.Ctx(st.sessions, st.stores, st.settings, st.secrets, None)
        report = await pipeline.run_ingest(ctx, rid)
        assert report["teams"] == 2
        async with st.sessions() as s:
            assert {t.name for t in (await s.execute(select(Team))).scalars()} == {"Альфа", "Бета"}


async def test_folder_used_by_a_run_but_never_reserved_is_adopted_as_used(tmp_path):
    """Runs made before batches existed (or by the admin tool) point at a folder nobody reserved: it must show as 'used', not 'outside'."""
    from core.db import Run
    c, _ = await seeded_client(tmp_path)
    async with c:
        await login(c)
        st = c._transport.app.state.fedlab
        await st.stores.data.put("incoming/demo-unzipped/Альфа_101_assignsubmission_file/w.pdf", make_pdf([TEXT]))
        async with st.sessions() as s:
            s.add(Run(name="legacy", config={}, source={"kind": "incoming", "prefix": "incoming/demo-unzipped/"}))
            await s.commit()
        b = {x["name"]: x for x in (await c.get("/api/batches")).json()["batches"]}["demo-unzipped"]
        assert b["status"] == "used" and b["registered"] and b["used_by"] and b["usable"]
        await st.stores.data.put("incoming/demo-unzipped/Бета_102_assignsubmission_file/w.pdf", make_pdf([TEXT]))   # changed later
        b = {x["name"]: x for x in (await c.get("/api/batches")).json()["batches"]}["demo-unzipped"]
        assert b["modified_after_use"] and not b["usable"]
