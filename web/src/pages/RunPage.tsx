import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import type { CostInfo, DeleteEvent, DeleteProgress, IngestEvent, ItemStatus, Me, Run, TeamRow } from "../types";
import StatusBadge from "../components/StatusBadge";
import RunConfigCard from "../components/RunConfigCard";
import ExportCard, { ExportActions } from "../components/ExportCard";
import Switch from "../components/Switch";
import BackLink from "../components/BackLink";
import SourceFiles from "../components/SourceFiles";
import IngestBar from "../components/IngestBar";
import DeleteBar from "../components/DeleteBar";

// Stage 1 (ingest) is a run-level job; the others are per-team units that the admin starts.
const FLAG_HELP: Record<string, string> = {
  "s2:HIDDEN_TEXT": "Stage 2: invisible or tiny text in the PDF text layer",
  "s2:INJECTION_SUSPECTED": "Stage 2: phrases that try to steer the grader (see the Pre-check card of the work)",
  "s3:MANIPULATION": "Stage 3: the AI-detection model saw an attempt to influence the checker",
  "s2:PARTIALLY_READABLE": "Stage 2: some slides had no usable text layer (image-only or garbled); their text, if any, came from OCR",
};

const STAGES: { id: string; label: string; available: boolean }[] = [  // 'available' = implemented
  { id: "s1_ingest", label: "1 Upload", available: false },
  { id: "s2_extract", label: "2 Extract", available: true },
  { id: "s3_detect", label: "3 AI detect", available: true },
  { id: "s4_grade", label: "4 Grade", available: true },
];
const ORDER: ItemStatus[] = ["done", "running", "queued", "failed", "pending"];

// A custom hook (name starts with "use"): reusable logic that can call other hooks.
// It opens ONE long-lived HTTP connection (Server-Sent Events). Ingest-progress ticks update a
// local state directly (they arrive every 2 s and must not trigger refetches); delete-progress
// ticks do the same; every other event tells React Query that run/teams data is stale -> they refetch.
function useRunEvents(runId: string, onIngest: (p: IngestEvent | null) => void, onDelete: (p: DeleteEvent) => void) {
  const qc = useQueryClient();
  const timer = useRef<number>();
  useEffect(() => {
    const es = new EventSource(`/api/runs/${runId}/events`);
    es.onmessage = (e) => {
      let ev: { type?: string; status?: string } & Partial<IngestEvent> & Partial<Omit<DeleteEvent, "type" | "status">>;
      try { ev = JSON.parse(e.data); } catch { return; }
      if (ev?.type === "ingest") { onIngest(ev as IngestEvent); return; }
      if (ev?.type === "delete") { onDelete(ev as DeleteEvent); return; }
      if (ev?.type === "run" && ev.status !== "ingesting") onIngest(null);  // ready / failed: drop the bar
      window.clearTimeout(timer.current); // debounce: a burst of events causes one refetch
      timer.current = window.setTimeout(() => {
        qc.invalidateQueries({ queryKey: ["run", runId] });
        qc.invalidateQueries({ queryKey: ["teams", runId] });
      }, 300);
    };
    return () => es.close(); // cleanup runs when the component goes away: no leaked connections
  }, [runId, qc, onIngest, onDelete]);
}

export default function RunPage({ me }: { me: Me }) {
  const { runId = "" } = useParams(); // the ":runId" part of the URL
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [ingest, setIngest] = useState<IngestEvent | null>(null);
  const [delConfirm, setDelConfirm] = useState("");
  const [deleting, setDeleting] = useState<DeleteProgress | null>(null);
  useEffect(() => { setDeleting(null); }, [runId]);   // a different run starts with a clean strip
  // Delete events update the local strip directly (like ingest ticks). The final "done" event means
  // the run no longer exists: refresh the list and go back to the Runs page.
  const onDeleteEvent = useCallback((ev: DeleteEvent) => {
    if (ev.status === "done") { qc.invalidateQueries({ queryKey: ["runs"] }); navigate("/"); return; }
    setDeleting((prev) => {
      const base: DeleteProgress = prev ?? { stages: {} };
      if (ev.status === "error") return { ...base, error: ev.error ?? "Deletion failed" };
      if (ev.stage) return { ...base, stages: { ...base.stages, [ev.stage]: { done: ev.done ?? 0, total: ev.total ?? 0 } } };
      return base;
    });
  }, [qc, navigate]);
  useRunEvents(runId, setIngest, onDeleteEvent);

  const run = useQuery({ queryKey: ["run", runId], queryFn: () => api.get<Run>(`/api/runs/${runId}`) });
  const teams = useQuery({ queryKey: ["teams", runId], queryFn: () => api.get<TeamRow[]>(`/api/runs/${runId}/teams`) });
  // Safety net: a 404 means the run was deleted (its "done" event may have been missed, for example
  // across a reconnect). Refresh the list and show the user the Runs page instead of an error.
  useEffect(() => {
    if (run.isError && run.error instanceof ApiError && run.error.status === 404) {
      qc.invalidateQueries({ queryKey: ["runs"] });
      navigate("/");
    }
  }, [run.isError, run.error, qc, navigate]);

  const cost = useQuery({ queryKey: ["cost", runId], queryFn: () => api.get<CostInfo>(`/api/runs/${runId}/cost`), refetchInterval: 5000 });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["run", runId] }); qc.invalidateQueries({ queryKey: ["teams", runId] }); };
  const start = useMutation({
    mutationFn: (v: { stage: string; only_failed: boolean }) =>
      api.post<{ queued: number }>(`/api/runs/${runId}/stages/${v.stage}/start`, { only_failed: v.only_failed }),
    onSuccess: refresh,
  });

  const setOcr = useMutation({
    mutationFn: (on: boolean) => api.put(`/api/runs/${runId}/config`, { config: { ...run.data!.config, ocr_enabled: on } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["run", runId] }),
  });
  const runAll = useMutation({ mutationFn: (only_failed: boolean) => api.post<{ queued: number }>(`/api/runs/${runId}/run-all`, { only_failed }), onSuccess: refresh });
  // Same confirmation pattern as clearing the cache in Settings: a typed word + a browser confirm.
  // The POST only queues the staged deletion; the bars fill from SSE events, and the "done" event
  // sends the user back to the Runs page.
  const delRun = useMutation({
    mutationFn: () => api.post<{ queued: boolean }>(`/api/runs/${runId}/delete`, { confirm: delConfirm }),
    onSuccess: () => {
      setDelConfirm("");
      setDeleting((prev) => (prev ? { ...prev, error: null } : prev));   // a retry clears the old error
      qc.invalidateQueries({ queryKey: ["run", runId] });
      qc.invalidateQueries({ queryKey: ["runs"] });
    },
  });

  if (run.isLoading) return <p className="muted">Loading…</p>;
  if (run.isError || !run.data) return <p className="error">{String(run.error)}</p>;
  const r = run.data;
  const admin = me.role === "admin";
  const total = r.teams ?? 0;

  const anyFailed = (r.stages?.["s4_grade"]?.failed ?? 0) + (r.stages?.["s2_extract"]?.failed ?? 0) > 0;
  const noteShown = r.note && !/unmatched in worksheet: \[\]$/.test(r.note);     // hide the "nothing unmatched" noise
  const detectOff = !!r.uses && !r.uses.detect_enabled;

  const stageCell = (s: (typeof STAGES)[number]) => {
    const c = r.stages?.[s.id] ?? {};
    const done = c.done ?? 0;
    const pct = total ? Math.round((done / total) * 100) : 0;
    const off = s.id === "s3_detect" && detectOff;
    return (
      <div className={`stage-cell ${s.available || s.id === "s1_ingest" ? "" : "off"} ${off ? "off" : ""}`} key={s.id}>
        <div className="stage-top"><strong>{s.label}</strong><span className="muted small">{off ? "off in Settings" : `${done}/${total}`}</span></div>
        <div className="bar"><div style={{ width: `${pct}%` }} /></div>
        <div className="stage-foot">
          <span className="muted small">{off ? "" : ORDER.filter((k) => k !== "done" && c[k]).map((k) => `${c[k]} ${k}`).join(" · ") || (done === total && total > 0 ? "complete" : done ? "" : "not started")}</span>
          {s.id === "s2_extract" && (
            <Switch checked={r.config.ocr_enabled} disabled={!admin || setOcr.isPending} onChange={(on) => setOcr.mutate(on)} label="OCR"
              title="Recognises only the slides that have no usable text (empty or unreadable); slides with a text layer are kept. About 0.13 ₽ per slide, cached per slide; the per-work page limit and the budget are on the Settings page." />)}
          {admin && s.available && total > 0 && !off && (
            <span className="spacer-inline">
              <button className="btn sm" disabled={start.isPending} onClick={() => start.mutate({ stage: s.id, only_failed: false })}>{done ? "Resume / run" : "Run"}</button>
              {(c.failed ?? 0) > 0 && <button className="btn sm" onClick={() => start.mutate({ stage: s.id, only_failed: true })}>Retry failed</button>}
            </span>)}
        </div>
      </div>
    );
  };

  return (
    <>
      <div className="run-head">
        <div>
          <BackLink to="/">Runs</BackLink>
          <h1 style={{ margin: ".1rem 0" }}>{r.name} <span className={`badge ${r.status}`}>{r.status}</span></h1>
          <div className="muted small">
            {total} works · grading {r.uses?.grade.prompt} × {r.uses?.grade.model}{detectOff ? " · AI detection off" : ` · AI detection ${r.uses?.detect.prompt}`}
            {cost.data && ` · spent ${cost.data.spent_rub.toFixed(2)} of ${cost.data.cap_rub} ₽`}
          </div>
          {noteShown && <div className="warn small">{r.note}</div>}
        </div>
        <span className="spacer" />
        <div className="actions">
          {admin && total > 0 && r.status === "ready" && (
            <>
              <button className="btn primary" disabled={runAll.isPending} onClick={() => runAll.mutate(false)}
                title="Stages 2 → 3 (if switched on in Settings) → 4 → export, for every work. Finished work is skipped.">▶ Resume / run all stages</button>
              {anyFailed && <button className="btn" onClick={() => runAll.mutate(true)}>Retry failed</button>}
            </>)}
          <ExportActions runId={runId} admin={admin} />
        </div>
      </div>
      {r.status === "ingesting" && <IngestBar progress={ingest} />}
      {runAll.isError && <p className="error">{String(runAll.error)}</p>}
      {start.isError && <p className="error">{String(start.error)}</p>}

      {admin && <SourceFiles run={r} onChange={refresh} />}
      <RunConfigCard run={r} admin={admin} />

      <div className="stage-strip" style={{ marginTop: "var(--gap)" }}>{STAGES.map(stageCell)}</div>

      <div className="row" style={{ margin: "1rem 0 .3rem" }}><h2 style={{ margin: 0 }}>Works ({total})</h2></div>
      <div className="tscroll"><table className="compact">
        <thead><tr><th>Team</th><th>File</th><th>Slides</th><th>Chars</th><th>Pre-check</th><th>AI prob.</th><th>Flags</th><th>Grade</th></tr></thead>
        <tbody>
          {teams.data?.map((t) => {
            const st = t.stages["s2_extract"];
            return (
              <tr key={t.id}>
                <td className="team"><Link to={`/runs/${runId}/teams/${t.id}`}>{t.name || "(no name)"}</Link>
                  {t.csv_matched === false && <span className="badge warnbadge" title="This team name was not found in the Moodle worksheet, so its grade cannot be written there">no match</span>}</td>
                <td className="muted file" title={t.filename}>{t.filename}{!t.is_pdf && " ⚠ not PDF"}</td>
                <td>{t.slide_count ?? "–"}</td>
                <td className={`nowrap ${t.over_char_limit ? "warn" : ""}`}>
                  {t.chars_with_spaces ?? "–"}
                  {t.over_char_limit && <span className="badge warnbadge" title={`Limit ${t.char_limit} chars with spaces exceeded. The work is still graded; criterion 6 takes it into account.`}> ⚠ over limit</span>}
                </td>
                <td>{st && st.status !== "done" ? <StatusBadge status={st.status} title={st.error ?? ""} />       // stage 2 still running / failed
                  : t.precheck_status ? <span className={`badge ${t.precheck_status}`} title={t.precheck_code ?? "Technical check passed"}>{t.precheck_code ?? t.precheck_status}</span> : "–"}</td>
                <td title="Informational only; never changes the grade">{t.ai_probability != null ? `${Math.round(t.ai_probability * 100)}%` : t.stages["s3_detect"] ? <StatusBadge status={t.stages["s3_detect"].status} title={t.stages["s3_detect"].error ?? ""} /> : "–"}</td>
                <td title="Reviewer hints (stage 2 / stage 3): manipulation attempts, partially readable slides. They never change the grade.">{(t.review_flags ?? []).map((f) => <span key={f} className="badge warnbadge" title={FLAG_HELP[f] ?? f}>{f.replace(/^s\d:/, "")}</span>)}</td>
                <td className="nowrap">{t.grade_total != null ? <strong>{t.grade_total}{t.grade_status === "DISQUALIFIED" && " (DQ)"}</strong> : t.stages["s4_grade"] ? <StatusBadge status={t.stages["s4_grade"].status} title={t.stages["s4_grade"].error ?? ""} /> : "–"}</td>
              </tr>
            );
          })}
        </tbody>
      </table></div>

      <div style={{ marginTop: "1rem" }}>
        <ExportCard runId={runId} />
      </div>

      {admin && (
        <div className="card danger" style={{ marginTop: "var(--gap)" }}>
          <h3>⚠ Danger zone · delete this run</h3>
          {(() => {
            const showBar = r.status === "deleting" || deleting != null;
            const showForm = !showBar || !!deleting?.error;   // after a failure the run can be deleted again
            return (
              <>
                {showBar && <DeleteBar progress={deleting ?? { stages: {} }} />}
                {!showBar && <p className="small">Deletes the run “{r.name}” completely: its works, stage history and cost records, plus all its files
                  in both buckets (sources <code>input/{r.id}/</code>, intermediate <code>_work/{r.id}/</code> and the exported results <code>{r.id}/</code>).
                  The shared answer cache and the source batches are kept, so other runs are not affected. <strong>This cannot be undone.</strong></p>}
                {showForm && (
                  <p className="row">
                    <input placeholder="type DELETE to confirm" value={delConfirm} onChange={(ev) => setDelConfirm(ev.target.value)} style={{ minWidth: "12rem" }} />
                    <button className="btn danger-btn" disabled={delRun.isPending || delConfirm !== "DELETE"}
                      onClick={() => { if (window.confirm(`Really delete run “${r.name}” and all its files? This cannot be undone.`)) delRun.mutate(); }}>Delete run</button>
                    {delRun.isError && <span className="error">{String(delRun.error)}</span>}
                  </p>
                )}
              </>
            );
          })()}
        </div>
      )}
    </>
  );
}
