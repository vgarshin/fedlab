import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import Switch from "../components/Switch";
import type { CacheInfo, ModelRow, SettingsData } from "../types";

// One page for everything that is "global": which model/prompt each stage uses, whether AI detection runs,
// and the wording/format of exported files. Changes apply to the NEXT stage start / export.
export default function SettingsPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["settings"], queryFn: () => api.get<SettingsData>("/api/settings") });
  const models = useQuery({ queryKey: ["models"], queryFn: () => api.get<ModelRow[]>("/api/models") });
  const [e, setE] = useState<SettingsData["export"] | null>(null);
  const [cap, setCap] = useState<string>("");
  const [ocrPages, setOcrPages] = useState<string>("");
  const cache = useQuery({ queryKey: ["cache"], queryFn: () => api.get<CacheInfo>("/api/settings/cache") });
  const [wipe, setWipe] = useState({ llm: true, ocr: false, confirm: "" });
  const clear = useMutation({
    mutationFn: () => api.post<{ removed: Record<string, number> }>("/api/settings/cache/clear", wipe),
    onSuccess: () => { setWipe({ ...wipe, confirm: "" }); qc.invalidateQueries({ queryKey: ["cache"] }); },
  });
  useEffect(() => { if (q.data) { setE(q.data.export); setCap(String(q.data.budget.run_cap_rub)); setOcrPages(String(q.data.ocr.max_pages)); } }, [q.data]);
  const save = useMutation({
    mutationFn: (body: object) => api.put("/api/settings", body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["settings"] }); qc.invalidateQueries({ queryKey: ["run"] }); },
  });
  const s = q.data;
  if (!s || !e) return <p className="muted">Loading…</p>;
  const enabled = (models.data ?? []).filter((m) => m.enabled);
  const modelSel = (key: "grade_model" | "detect_model") => (
    <select value={s.defaults[key]} onChange={(ev) => save.mutate({ defaults: { [key]: ev.target.value } })}>
      {enabled.map((m) => <option key={m.id} value={m.id}>{m.label || m.id}</option>)}
    </select>);
  const promptSel = (key: "grade_prompt" | "detect_prompt", detect: boolean) => (
    <select value={s.defaults[key]} onChange={(ev) => save.mutate({ defaults: { [key]: ev.target.value } })}>
      {s.prompt_names.filter((n) => detect === n.includes("detect")).map((n) => <option key={n} value={n}>{n} (latest published version)</option>)}
    </select>);
  return (
    <>
      <h1>Settings</h1>
      {save.isError && <p className="error">{String(save.error)}</p>}

      <div className="settings-grid">
      <div className="card">
        <h3>Stages</h3>
        <Switch checked={s.stages.detect_enabled} onChange={(on) => save.mutate({ stages: { detect_enabled: on } })}
          label={<><strong>Stage 3 · AI detection</strong> <span className="muted">(informational; never changes a grade). When off, “Run all” skips it and runs show it as off.</span></>} />
      </div>

      <div className="card wide">
        <h3>Model and prompt per stage</h3>
        <div className="model-row"><strong>Grading</strong>{modelSel("grade_model")}{promptSel("grade_prompt", false)}</div>
        <div className="model-row"><strong>AI detection</strong>{modelSel("detect_model")}{promptSel("detect_prompt", true)}</div>
        <p className="muted small">Used by every run when a stage starts. Publish a new prompt version on the Prompts page and the latest published version is picked up automatically. Models are switched on/off on the Models page. Changing a choice makes the next “Run” recompute only the results that used the old choice.</p>
      </div>

      <div className="card">
        <h3>OCR</h3>
        <p><label>Maximum slides recognised by OCR per work <input type="number" min={1} max={500} value={ocrPages} onChange={(ev) => setOcrPages(ev.target.value)} style={{ minWidth: "6rem", width: "6rem" }} /></label>{" "}
          <button className="btn" disabled={save.isPending || !(Number(ocrPages) >= 1)} onClick={() => save.mutate({ ocr: { max_pages: Number(ocrPages) } })}>Save</button></p>
        <p className="muted small">OCR is switched on per run in its Stage 2 block. When it is on, only the slides that have no usable text of their own (empty or unreadable) are recognised; slides with a text layer are kept as they are.
          If one work needs more slides than this limit, OCR is skipped for that work (it gets a warning, and fails the technical filter only if nothing readable is left). Price: {s.ocr.price_per_page_rub} ₽ per slide, results cached per slide.
          OCR spending counts toward the run’s budget cap below: before a work is recognised, the run must still have room for its OCR cost, otherwise that work stops with “budget cap reached”.</p>
      </div>

      <div className="card">
        <h3>Budget limits</h3>
        <p><label>Spending cap per run, ₽ <input type="number" min={1} value={cap} onChange={(ev) => setCap(ev.target.value)} style={{ minWidth: "7rem", width: "7rem" }} /></label>{" "}
          <button className="btn" disabled={save.isPending || !(Number(cap) > 0)} onClick={() => save.mutate({ budget: { run_cap_rub: Number(cap) } })}>Save</button></p>
        <p className="muted small">The cap covers everything a run spends: grading, AI detection and OCR. It is checked before every paid LLM call; when it is reached, remaining works stop with a “budget cap reached” error (raise the cap and press Resume to continue). Applies to all runs.</p>
      </div>

      <div className="card">
        <h3>Export wording and format</h3>
        <p><label>First line of the participant feedback and of “Feedback comments” in the worksheet<br />
          <input value={e.score_line} onChange={(ev) => setE({ ...e, score_line: ev.target.value })} style={{ width: "28rem" }} /></label>
          <span className="muted small"> use {"{total}"} for the 0–100 grade</span></p>
        <p><label>Heading above the feedback text<br /><input value={e.feedback_header} onChange={(ev) => setE({ ...e, feedback_header: ev.target.value })} style={{ width: "28rem" }} /></label></p>
        <p><label>Worksheet CSV delimiter<br />
          <select value={e.csv_mode} onChange={(ev) => setE({ ...e, csv_mode: ev.target.value as SettingsData["export"]["csv_mode"] })}>
            <option value="auto">same as the uploaded file</option><option value="comma">comma</option><option value="tab">tab</option>
            <option value="both">both: same as uploaded + a second file with the other delimiter</option></select></label></p>
        <button className="btn primary" disabled={save.isPending} onClick={() => save.mutate({ export: e })}>Save export settings</button> {save.isSuccess && <span className="muted">saved</span>}
      </div>

      </div>

      <div className="card danger">
        <h3>⚠ Danger zone · clear cache</h3>
        <p className="small">The cache holds answers already paid for: <strong>LLM answers</strong> {cache.data ? `(${cache.data.llm.objects} objects, ${(cache.data.llm.bytes / 1048576).toFixed(1)} MB)` : ""} and <strong>OCR pages</strong> {cache.data ? `(${cache.data.ocr.objects} objects, ${(cache.data.ocr.bytes / 1048576).toFixed(1)} MB)` : ""}.
          While it exists, running the same work with the same prompt and model again returns the stored answer instantly for 0 ₽ and with the same result.
          After clearing, the next run of those works calls the model and <strong>costs money again</strong> (and may give slightly different grades). Results already stored in runs and exports are not deleted.</p>
        <div><Switch checked={wipe.llm} onChange={(on) => setWipe({ ...wipe, llm: on })} label="LLM answers (grading, AI detection)" /></div>
        <div style={{ marginTop: ".3rem" }}><Switch checked={wipe.ocr} onChange={(on) => setWipe({ ...wipe, ocr: on })} label="OCR pages" /></div>
        <p className="row"><input placeholder="type CLEAR to confirm" value={wipe.confirm} onChange={(ev) => setWipe({ ...wipe, confirm: ev.target.value })} style={{ minWidth: "12rem" }} />
          <button className="btn danger-btn" disabled={clear.isPending || wipe.confirm !== "CLEAR" || !(wipe.llm || wipe.ocr)}
            onClick={() => { if (window.confirm("Really delete the selected cache? Re-running works will cost money again.")) clear.mutate(); }}>Clear cache</button>
          {clear.isSuccess && <span className="muted">removed: {Object.entries(clear.data.removed).map(([k, n]) => `${n} ${k}`).join(", ")}</span>}
          {clear.isError && <span className="error">{String(clear.error)}</span>}</p>
      </div>
    </>
  );
}
