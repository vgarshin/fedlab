import httpx
import pytest
from httpx import ASGITransport

from api.deps import AppState
from api.main import create_app
from tests.conftest import make_pdf, make_zip
from tests.helpers import make_ctx

H = {"X-Requested-With": "fedlab"}


class FakeQueue:
    def __init__(self):
        self.jobs = []

    async def enqueue_job(self, name, *args, **kw):
        self.jobs.append((name, args))


@pytest.fixture
async def client(tmp_path):
    c, q = await seeded_client(tmp_path)
    async with c:
        c.queue = q
        yield c


async def login(c, email="admin@example.com"):
    r = await c.get("/api/auth/dev-login", params={"email": email})
    assert r.status_code in (302, 307)


async def test_requires_auth(client):
    assert (await client.get("/api/runs")).status_code == 401
    assert (await client.get("/api/health")).status_code == 200


async def test_csrf_header_required_for_writes(client):
    await login(client)
    assert (await client.post("/api/runs", json={"name": "x"})).status_code == 403
    assert (await client.post("/api/runs", json={"name": "x"}, headers=H)).status_code == 201


async def test_viewer_is_read_only(client):
    await login(client)  # creates admin
    r = await client.post("/api/users", json={"email": "v@example.com", "role": "viewer"}, headers=H)
    assert r.status_code == 201
    client.cookies.clear()
    await client.get("/api/auth/dev-login", params={"email": "v@example.com"})
    assert (await client.get("/api/runs")).status_code == 200
    assert (await client.post("/api/runs", json={"name": "x"}, headers=H)).status_code == 403
    assert (await client.get("/api/users")).status_code == 403


async def test_upload_enqueues_ingest(client):
    await login(client)
    run = (await client.post("/api/runs", json={"name": "r1"}, headers=H)).json()
    z = make_zip({"Team_1": ("w.pdf", make_pdf(["hello world text " * 10]))})
    r = await client.post(f"/api/runs/{run['id']}/upload", files={"submissions": ("s.zip", z)}, headers=H)
    assert r.status_code == 200 and client.queue.jobs == [("ingest_run_job", (run["id"],))]
    bad = await client.post(f"/api/runs/{run['id']}/upload", files={"submissions": ("s.zip", b"nope")}, headers=H)
    assert bad.status_code == 400


async def test_artifact_name_is_validated(client):
    await login(client)
    run = (await client.post("/api/runs", json={"name": "r"}, headers=H)).json()
    assert (await client.get(f"/api/runs/{run['id']}/teams/1/artifacts/..%2Fsecret.json")).status_code in (400, 404)


async def seeded_client(tmp_path):
    from core import registry
    ctx = await make_ctx(tmp_path)
    ctx.settings.models = [{"id": "m1", "provider": "yandex", "model": "x", "price_in_per_1m": 300, "price_out_per_1m": 500}]
    await registry.seed(ctx.sessions, ctx.settings)          # real prompts/ directory
    ctx.settings.defaults.grade_model = ctx.settings.defaults.detect_model = "m1"
    q = FakeQueue()
    state = AppState(ctx.settings, ctx.secrets, ctx.sessions, ctx.stores, q, None, None)
    app = create_app(state)
    app.state.fedlab = state
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t"), q


async def test_prompts_models_and_run_defaults(tmp_path):
    c, _ = await seeded_client(tmp_path)
    async with c:
        await login(c)
        names = {p["name"] for p in (await c.get("/api/prompts")).json()}
        assert {"grade_long", "grade_short", "ai_detect"} <= names
        run = (await c.post("/api/runs", json={"name": "r"}, headers=H)).json()
        detail = (await c.get(f"/api/runs/{run['id']}")).json()
        assert detail["uses"]["grade"]["prompt"] == "grade_short v1" and detail["uses"]["detect_enabled"] is True


async def test_prompt_edit_creates_new_version_and_publish(tmp_path):
    c, _ = await seeded_client(tmp_path)
    async with c:
        await login(c)
        old = next(p for p in (await c.get("/api/prompts")).json() if p["name"] == "grade_short")
        new = (await c.post("/api/prompts", json={"name": "grade_short", "text": "x" * 50, "note": "tweak"}, headers=H)).json()
        assert new["version"] == old["version"] + 1 and new["status"] == "draft" and new["kind"] == "grade"
        pub = (await c.post(f"/api/prompts/{new['id']}/publish", headers=H)).json()
        assert pub["status"] == "published"
        assert (await c.get(f"/api/prompts/{old['id']}")).json()["text"] != "x" * 50      # old version untouched


async def test_cost_estimate_and_model_patch(tmp_path):
    c, _ = await seeded_client(tmp_path)
    async with c:
        await login(c)
        run = (await c.post("/api/runs", json={"name": "r"}, headers=H)).json()
        est = (await c.get(f"/api/runs/{run['id']}/estimate")).json()
        assert est["cap_rub"] == 1000 and len(est["lines"]) == 2
        assert (await c.get(f"/api/runs/{run['id']}/cost")).json()["spent_rub"] == 0
        r = await c.patch("/api/models/m1", json={"price_in_per_1m": 123.0}, headers=H)
        assert r.json()["price_in_per_1m"] == 123.0


async def test_settings_drive_stages_and_defaults(tmp_path):
    c, q = await seeded_client(tmp_path)
    async with c:
        await login(c)
        s = (await c.get("/api/settings")).json()
        assert s["defaults"]["grade_prompt"] == "grade_short" and s["stages"]["detect_enabled"] is True
        r = await c.put("/api/settings", json={"stages": {"detect_enabled": False}, "export": {"csv_mode": "tab"}}, headers=H)
        assert r.json()["stages"]["detect_enabled"] is False and r.json()["export"]["csv_mode"] == "tab"
        run = (await c.post("/api/runs", json={"name": "r"}, headers=H)).json()
        detail = (await c.get(f"/api/runs/{run['id']}")).json()
        assert detail["uses"]["detect_enabled"] is False
        assert (await c.post(f"/api/runs/{run['id']}/stages/s3_detect/start", json={}, headers=H)).status_code == 409
        assert (await c.put("/api/settings", json={"defaults": {"grade_model": "nope"}}, headers=H)).status_code == 400
        # run-all needs a finished stage 1
        assert (await c.post(f"/api/runs/{run['id']}/run-all", json={}, headers=H)).status_code == 409


async def test_analytics_latest_run_default_and_histograms(tmp_path):
    from sqlalchemy import select as sel
    from core.db import Run, Team, LlmCall
    from api.routes.analytics import histogram
    # pure histogram logic
    h = histogram([("a", 0), ("b", 55), ("c", 59), ("d", 100)], 10, upper=100)
    assert [b["count"] for b in h] == [1, 0, 0, 0, 0, 2, 0, 0, 0, 1] and len(h) == 10
    assert histogram([("a", 100), ("b", 12500)], 2000)[-1]["count"] == 1
    c, _ = await seeded_client(tmp_path)
    async with c:
        await login(c)
        assert (await c.get("/api/analytics")).json()["run"] is None
        r1 = (await c.post("/api/runs", json={"name": "old"}, headers=H)).json()
        r2 = (await c.post("/api/runs", json={"name": "new"}, headers=H)).json()
        st = c._transport.app.state.fedlab
        async with st.sessions() as s:
            for name, g, status, chars in (("A", 80, "EVALUATED", 5000), ("B", 60, "EVALUATED", 12000), ("C", 0, "DISQUALIFIED", 100), ("D", None, None, None)):
                s.add(Team(run_id=r2["id"], name=name, slug=name, grade_total=g, grade_status=status, chars_with_spaces=chars))
            s.add(LlmCall(run_id=r2["id"], stage="s4_grade", model_id="m1", cost_rub=1.5))
            await s.commit()
        d = (await c.get("/api/analytics")).json()
        assert d["run"]["id"] == r2["id"] and [r["id"] for r in d["runs"]] == [r2["id"], r1["id"]]
        assert d["kpi"] == {"teams": 4, "members": None, "graded": 2, "disqualified": 1, "not_graded": 1, "avg_grade": 70.0,
                            "median_grade": 80, "spent_rub": 1.5, "cost_per_work_rub": 0.38}
        assert [t["name"] for t in d["top"]] == ["A", "B"] and [t["name"] for t in d["bottom"]] == ["B", "A"]
        assert d["over_limit"] == 1 and sum(b["count"] for b in d["grade_hist"]) == 2
        assert (await c.get(f"/api/analytics?run_id={r1['id']}")).json()["kpi"]["teams"] == 0
        assert (await c.get("/api/analytics?run_id=999")).status_code == 404


async def test_ocr_setting_and_cache_clear_needs_confirmation(tmp_path):
    c, _ = await seeded_client(tmp_path)
    async with c:
        await login(c)
        st = c._transport.app.state.fedlab
        s = (await c.put("/api/settings", json={"ocr": {"max_pages": 25}}, headers=H)).json()
        assert s["ocr"]["max_pages"] == 25 and s["ocr"]["price_per_page_rub"] > 0
        await st.stores.data.put("_cache/llm/m/a.json", b"{}")
        await st.stores.data.put("_cache/ocr/x/p1.json", b"{}")
        await st.stores.data.put("_work/1/t/s4_grade.json", b"{}")
        info = (await c.get("/api/settings/cache")).json()
        assert info["llm"]["objects"] == 1 and info["ocr"]["objects"] == 1
        assert (await c.post("/api/settings/cache/clear", json={"llm": True}, headers=H)).status_code == 400          # no confirm
        assert (await c.post("/api/settings/cache/clear", json={"confirm": "CLEAR"}, headers=H)).status_code == 400   # nothing chosen
        r = await c.post("/api/settings/cache/clear", json={"llm": True, "confirm": "CLEAR"}, headers=H)
        assert r.json() == {"removed": {"llm": 1}}
        assert await st.stores.data.exists("_cache/ocr/x/p1.json") and await st.stores.data.exists("_work/1/t/s4_grade.json")


async def test_viewer_permissions_matrix(tmp_path):
    c, q = await seeded_client(tmp_path)
    async with c:
        await login(c)                                    # admin
        run = (await c.post("/api/runs", json={"name": "r"}, headers=H)).json()
        rid = run["id"]
        st = c._transport.app.state.fedlab
        from core.db import Team
        async with st.sessions() as s:
            t = Team(run_id=rid, name="A", slug="A", rel_dir="d/A_1_assignsubmission_file", filename="w.pdf", file_key="k")
            s.add(t)
            await s.commit()
            tid = t.id
        await st.stores.result.put(f"{rid}/d/A_1_assignsubmission_file/grading_result_feedback.txt", b"x")
        await st.stores.result.put(f"{rid}/grading_results.json", b"{}")
        await c.post("/api/users", json={"email": "v@example.com", "role": "viewer"}, headers=H)
        c.cookies.clear()
        await c.get("/api/auth/dev-login", params={"email": "v@example.com"})
        allowed = [f"/api/runs", f"/api/runs/{rid}", f"/api/runs/{rid}/teams", f"/api/runs/{rid}/teams/{tid}", f"/api/runs/{rid}/cost",
                   f"/api/runs/{rid}/estimate", f"/api/runs/{rid}/export", f"/api/runs/{rid}/teams/{tid}/export-files",
                   f"/api/runs/{rid}/export/file?path=grading_results.json", f"/api/runs/{rid}/export/zip",
                   f"/api/runs/{rid}/export/zip?team_id={tid}", "/api/analytics", "/api/auth/me", "/api/playground/options"]
        for url in allowed:
            assert (await c.get(url)).status_code == 200, url
        forbidden_get = ["/api/prompts", "/api/prompts/1", "/api/models", "/api/settings", "/api/settings/cache", "/api/users", "/api/batches"]
        for url in forbidden_get:
            assert (await c.get(url)).status_code == 403, url
        forbidden_post = [("post", "/api/runs", {"name": "x"}), ("post", f"/api/runs/{rid}/run-all", {}), ("post", f"/api/runs/{rid}/export", None),
                          ("post", f"/api/runs/{rid}/stages/s4_grade/start", {}), ("post", f"/api/runs/{rid}/stages/s2_extract/start", {}),
                          ("post", f"/api/runs/{rid}/reingest", None), ("post", f"/api/runs/{rid}/use-incoming", {"batch": "b"}), ("post", "/api/batches", {"name": "abc"}), ("delete", "/api/batches/abc", None),
                          ("put", f"/api/runs/{rid}/config", {"config": {"ocr_enabled": True}}), ("put", "/api/settings", {"stages": {"detect_enabled": False}}),
                          ("post", "/api/settings/cache/clear", {"llm": True, "confirm": "CLEAR"}), ("post", "/api/prompts", {"name": "grade_x", "text": "x" * 40}), ("patch", "/api/models/m1", {"enabled": False}),
                          ("post", "/api/users", {"email": "z@example.com"})]
        for method, url, body in forbidden_post:
            r = await getattr(c, method)(url, headers=H, **({"json": body} if body is not None else {}))
            assert r.status_code == 403, (method, url, r.status_code)
        up = await c.post(f"/api/runs/{rid}/upload", files={"submissions": ("s.zip", b"x")}, headers=H)
        assert up.status_code == 403 and q.jobs == []                       # nothing was queued by the viewer
        z = await c.get(f"/api/runs/{rid}/export/zip?team_id={tid}")
        import zipfile as zf, io as _io
        assert zf.ZipFile(_io.BytesIO(z.content)).namelist() == ["d/A_1_assignsubmission_file/grading_result_feedback.txt"]


async def test_playground_runs_with_fresh_flag_and_returns_full_input(tmp_path, monkeypatch):
    import api.routes.llm as llm_routes
    from core.db import Team
    from core.llm import Gateway
    from tests.fakes import FakeClient, good_grade
    from tests.conftest import make_pdf
    from core.stages.extract import run_extract  # noqa: F401
    c, _ = await seeded_client(tmp_path)
    async with c:
        await login(c)
        st = c._transport.app.state.fedlab
        st.settings.yandex.folder_id = "f"
        fake = FakeClient([good_grade(6), good_grade(9)])
        monkeypatch.setattr(llm_routes, "Gateway", lambda s, sec, sess, cache: Gateway(s, sec, sess, cache, clients={"yandex": fake}))
        run = (await c.post("/api/runs", json={"name": "r"}, headers=H)).json()
        async with st.sessions() as s:
            t = Team(run_id=run["id"], name="A", slug="A", rel_dir="d/A", filename="w.pdf", file_key="k")
            s.add(t)
            await s.commit()
            tid = t.id
        for name, obj in (("s2_pages.json", [{"number": 1, "text": "hello", "chars": 5}]), ("s2_metrics.json", {}), ("s2_precheck.json", {"status": "PASS", "warnings": []})):
            await st.stores.data.put(f"_work/{run['id']}/A/{name}", __import__("json").dumps(obj).encode())
        opts = (await c.get("/api/playground/options")).json()
        pid = next(p["id"] for p in opts["prompts"] if p["name"] == "grade_short")
        assert all("text" not in p for p in opts["prompts"]) and opts["models"] == [{"id": "m1", "label": "m1"}]
        body = {"run_id": run["id"], "team_id": tid, "prompt_id": pid, "model_id": "m1"}
        url = "/api/playground/run"
        # a viewer may use the Playground (cost is billed to the run, capped by the budget)
        await c.post("/api/users", json={"email": "pv@example.com", "role": "viewer"}, headers=H)
        a = (await c.post(url, json=body, headers=H)).json()
        assert a["parsed"]["total_score_100"] == 60 and a["cache_hit"] is False and "hello" in a["input_full"] and a["prompt"] == "grade_short v1"
        b = (await c.post(url, json=body, headers=H)).json()
        assert b["cache_hit"] is True and b["parsed"]["total_score_100"] == 60 and len(fake.calls) == 1        # identical request: cache
        f = (await c.post(url, json={**body, "fresh": True}, headers=H)).json()
        assert f["cache_hit"] is False and f["parsed"]["total_score_100"] == 90 and len(fake.calls) == 2
        # as a viewer: allowed, and the run's budget cap is enforced
        c.cookies.clear()
        await c.get("/api/auth/dev-login", params={"email": "pv@example.com"})
        fake.replies = [good_grade(5)]
        assert (await c.post(url, json={**body, "fresh": True}, headers=H)).status_code == 200
        st.settings.budget = {"run_cap_rub": 0.0001}
        r = await c.post(url, json={**body, "fresh": True}, headers=H)
        assert r.status_code == 402


async def test_api_keys_lifecycle_and_scopes(tmp_path):
    c, q = await seeded_client(tmp_path)
    async with c:
        await login(c)                                                   # admin browser session
        r = await c.post("/api/meta/keys", json={"name": "moodle-sync", "role": "viewer", "expires_days": 30}, headers=H)
        assert r.status_code == 201
        ro = r.json()
        assert ro["key"].startswith("fl_") and ro["prefix"] == ro["key"][:11] and "key_hash" not in ro
        listed = (await c.get("/api/meta/keys")).json()
        assert listed[0]["prefix"] == ro["prefix"] and "key" not in listed[0]                 # the key itself is never shown again
        full = (await c.post("/api/meta/keys", json={"name": "ci", "role": "admin"}, headers=H)).json()

        svc = httpx.AsyncClient(transport=c._transport, base_url="http://t")                    # a service: no cookie, only a key
        async with svc:
            assert (await svc.get("/api/runs")).status_code == 401
            assert (await svc.get("/api/runs", headers={"Authorization": "Bearer fl_wrong"})).status_code == 401
            ok = await svc.get("/api/auth/me", headers={"Authorization": f"Bearer {ro['key']}"})
            assert ok.status_code == 200 and ok.json()["auth"] == "key" and ok.json()["role"] == "viewer"
            assert (await svc.get("/api/runs", headers={"X-API-Key": ro["key"]})).status_code == 200      # second accepted header form
            # read-only key: reads yes, writes no (and no CSRF header is needed for key requests)
            assert (await svc.post("/api/runs", json={"name": "x"}, headers={"Authorization": f"Bearer {ro['key']}"})).status_code == 403
            assert (await svc.get("/api/prompts", headers={"Authorization": f"Bearer {ro['key']}"})).status_code == 403
            # admin key: writes without the browser CSRF header
            made = await svc.post("/api/runs", json={"name": "from service"}, headers={"Authorization": f"Bearer {full['key']}"})
            assert made.status_code == 201
            # but no key can manage keys
            assert (await svc.get("/api/meta/keys", headers={"Authorization": f"Bearer {full['key']}"})).status_code == 403
            assert (await svc.post("/api/meta/keys", json={"name": "evil"}, headers={"Authorization": f"Bearer {full['key']}"})).status_code == 403
            # reference needs admin
            assert (await svc.get("/api/meta/openapi", headers={"Authorization": f"Bearer {ro['key']}"})).status_code == 403
            # revoke => immediately dead
            assert (await c.post(f"/api/meta/keys/{ro['id']}/revoke", headers=H)).status_code == 200
            assert (await svc.get("/api/runs", headers={"Authorization": f"Bearer {ro['key']}"})).status_code == 401
        used = {k["name"]: k for k in (await c.get("/api/meta/keys")).json()}
        assert used["ci"]["last_used_at"] is not None and used["moodle-sync"]["revoked_at"] is not None


async def test_expired_key_is_rejected(tmp_path):
    from datetime import datetime, timedelta, timezone
    from core.db import ApiKey
    c, _ = await seeded_client(tmp_path)
    async with c:
        await login(c)
        k = (await c.post("/api/meta/keys", json={"name": "short", "expires_days": 1}, headers=H)).json()
        st = c._transport.app.state.fedlab
        async with st.sessions() as s:
            row = await s.get(ApiKey, k["id"])
            row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            await s.commit()
        assert (await c.get("/api/runs", headers={"Authorization": f"Bearer {k['key']}"}, cookies={})).status_code in (200, 401)
        svc = httpx.AsyncClient(transport=c._transport, base_url="http://t")
        async with svc:
            assert (await svc.get("/api/runs", headers={"Authorization": f"Bearer {k['key']}"})).status_code == 401


async def test_reference_lists_endpoints_with_roles(tmp_path):
    c, _ = await seeded_client(tmp_path)
    async with c:
        await login(c)
        doc = (await c.get("/api/meta/openapi")).json()
        ops = {(m.upper(), p): o for p, d in doc["paths"].items() for m, o in d.items()}
        assert ops[("GET", "/api/runs")]["x-role"] == "viewer"
        assert ops[("POST", "/api/runs")]["x-role"] == "admin"
        assert ops[("POST", "/api/meta/keys")]["x-role"].startswith("admin (browser")
        assert ops[("GET", "/api/health")]["x-role"] == "public"
        assert ops[("GET", "/api/playground/options")]["x-role"] == "viewer"
