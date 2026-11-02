import { useState } from "react";
import { useParams } from "react-router-dom";
import BackLink from "../components/BackLink";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import type { DetectArtifact, GradeArtifact, LlmCallRow, PageText, Precheck, TeamDetail, TeamFile } from "../types";
import StatusBadge from "../components/StatusBadge";

// Generic hook: load one stored JSON artifact of this team (only when the artifact exists).
function useArtifact<T>(runId: string, teamId: string, name: string, enabled: boolean) {
  return useQuery({
    queryKey: ["artifact", runId, teamId, name],
    queryFn: () => api.get<T>(`/api/runs/${runId}/teams/${teamId}/artifacts/${name}`),
    enabled,
  });
}

export default function TeamPage() {
  const { runId = "", teamId = "" } = useParams();
  const team = useQuery({ queryKey: ["team", runId, teamId], queryFn: () => api.get<TeamDetail>(`/api/runs/${runId}/teams/${teamId}`) });
  const has = (n: string) => !!team.data?.artifacts.includes(n);
  const pre = useArtifact<Precheck>(runId, teamId, "s2_precheck.json", has("s2_precheck.json"));
  const metrics = useArtifact<Record<string, unknown>>(runId, teamId, "s2_metrics.json", has("s2_metrics.json"));
  const pages = useArtifact<PageText[]>(runId, teamId, "s2_pages.json", has("s2_pages.json"));
  const calls = useQuery({ queryKey: ["calls", runId, teamId], queryFn: () => api.get<LlmCallRow[]>(`/api/runs/${runId}/teams/${teamId}/llm-calls`) });
  const files = useQuery({ queryKey: ["teamfiles", runId, teamId], queryFn: () => api.get<TeamFile[]>(`/api/runs/${runId}/teams/${teamId}/export-files`) });
  const [open, setOpen] = useState<string | null>(null);
  const raw = useArtifact<unknown>(runId, teamId, open ?? "", !!open);

  if (team.isLoading) return <p className="muted">Loading…</p>;
  if (team.isError || !team.data) return <p className="error">{String(team.error)}</p>;
  const t = team.data;

  return (
    <>
      <p style={{ margin: ".2rem 0 .6rem" }}><BackLink to={`/runs/${runId}`}>Run</BackLink></p>
      <h1>{t.name || "(no name)"}</h1>
      <p className="muted">{t.filename}{t.extra_files.length > 0 && ` · also submitted: ${t.extra_files.join(", ")}`}</p>

      <div className="stages">
        {Object.entries(t.stages).map(([id, s]) => (
          <div className="card" key={id}>
            <strong>{id}</strong> <StatusBadge status={s.status} />
            <div className="muted small">
              attempts {s.attempts ?? 0}{s.duration_ms != null && ` · ${s.duration_ms} ms`}{s.cache_hit && " · resumed from stored result"}
            </div>
            {s.error && <div className="error small">{s.error}</div>}
          </div>
        ))}
      </div>

      {pre.data && (
        <div className={`card ${pre.data.status === "FAIL" ? "fail" : pre.data.warnings.length ? "warncard" : ""}`}>
          <h3>Technical pre-check: <span className={`badge ${pre.data.status}`}>{pre.data.code ?? pre.data.status}</span></h3>
          {pre.data.reason && <p>{pre.data.reason}. No LLM call will be made for this work (0 points).</p>}
          {pre.data.warnings.length > 0 && <p className="warn">⚠ Warnings do not stop grading. HIDDEN_TEXT, INJECTION_SUSPECTED and PARTIALLY_READABLE are reviewer hints only: the grader does not see them; the rest are passed to the grader and reflected in criterion 6.</p>}
          {pre.data.warnings.length > 0 && <ul>{pre.data.warnings.map((w) => <li key={w} className="warn">{w}</li>)}</ul>}
        </div>
      )}

      <div className="split">
        <div>
          <h3>Submission</h3>
          {/* The PDF endpoint is same-origin, so the browser's built-in viewer can show it in an iframe. */}
          {t.is_pdf ? <iframe title="pdf" className="pdf" src={`/api/runs/${runId}/teams/${teamId}/file`} />
                    : <p className="warn">Not a PDF file: {t.filename}</p>}
        </div>
        <div>
          <h3>Metrics</h3>
          {metrics.data ? <pre>{JSON.stringify(metrics.data, null, 2)}</pre> : <p className="muted">Not computed yet.</p>}
        </div>
      </div>

      <DetectView runId={runId} teamId={teamId} ready={has("s3_detect.json")} />
      <GradeView runId={runId} teamId={teamId} ready={has("s4_grade.json")} />

      {calls.data && calls.data.length > 0 && (
        <details><summary>LLM calls ({calls.data.length}) · {calls.data.reduce((a, c) => a + c.cost_rub, 0).toFixed(3)} ₽</summary>
          <div className="tscroll"><table><thead><tr><th>Stage</th><th>Prompt</th><th>Model</th><th>Tokens in/out</th><th>₽</th><th>ms</th><th></th></tr></thead>
            <tbody>{calls.data.map((c) => <tr key={c.id}><td>{c.stage}</td><td>{c.prompt}</td><td>{c.model}</td><td>{c.prompt_tokens} / {c.completion_tokens}</td><td>{c.cost_rub.toFixed(4)}</td><td>{c.latency_ms}</td><td>{c.cache_hit ? "cache" : c.status === "error" ? <span className="error" title={c.error ?? ""}>error</span> : ""}</td></tr>)}</tbody></table></div>
        </details>
      )}

      <div className="card">
        <div className="row"><h3 style={{ margin: 0 }}>Result files of this work</h3><span className="spacer" />
          {files.data && files.data.length > 0 && <a className="btn" href={`/api/runs/${runId}/export/zip?team_id=${teamId}`}>⬇ Download all (zip)</a>}</div>
        {files.data && files.data.length > 0 ? (
          <ul className="small">{files.data.map((f) => (
            <li key={f.path}><a href={`/api/runs/${runId}/export/file?path=${encodeURIComponent(f.path)}`}>{f.path.split("/").pop()}</a>
              <span className="muted"> {(f.bytes / 1024).toFixed(1)} KB · {f.path.startsWith("pipeline_results/") ? "pipeline_results/…" : "in the Moodle folder of the work"}</span></li>))}</ul>
        ) : <p className="muted small">Not exported yet: build the export on the run page after grading.</p>}
      </div>

      <h3>Extracted text by slide</h3>
      {pages.data?.map((p) => (
        <details key={p.number} open={false}>
          <summary>Slide {p.number} · {p.chars} chars{p.empty && " · empty"}{p.source === "ocr" && " · OCR"}</summary>
          <p className="slide-text">{p.text || "—"}</p>
        </details>
      ))}

      <h3>All stored artifacts</h3>
      <ul className="artifacts">
        {t.artifacts.map((a) => (
          <li key={a}><button className="link" onClick={() => setOpen(open === a ? null : a)}>{a}</button></li>
        ))}
      </ul>
      {open && <pre>{raw.isLoading ? "Loading…" : JSON.stringify(raw.data, null, 2)}</pre>}
    </>
  );
}

function DetectView({ runId, teamId, ready }: { runId: string; teamId: string; ready: boolean }) {
  const d = useArtifact<DetectArtifact>(runId, teamId, "s3_detect.json", ready);
  if (!d.data) return null;
  const r = d.data.result;
  return (
    <div className="card">
      <h3>AI detection <span className="muted small">({d.data.meta.prompt} × {d.data.meta.model}; informational only, never changes the grade)</span></h3>
      {d.data.skipped ? <p className="muted">Skipped: {d.data.reason}</p> : r && (
        <>
          <p><strong>{Math.round(r.ai_probability * 100)}%</strong> · verdict {r.verdict} · confidence {r.confidence}</p>
          <p>{r.rationale}</p>
          {r.manipulation_attempt?.found && (
            <p className="warn">⚠ Possible attempt to influence the checker (slides {r.manipulation_attempt.slides.join(", ") || "?"}): {r.manipulation_attempt.evidence} Informational: the grade is not affected, a reviewer decides.</p>
          )}
          <details><summary>Signals</summary><ul>{Object.entries(r.signals).map(([k, v]) => <li key={k}>{k}: {v.score.toFixed(2)} — {v.evidence}</li>)}</ul></details>
        </>
      )}
    </div>
  );
}

function GradeView({ runId, teamId, ready }: { runId: string; teamId: string; ready: boolean }) {
  const g = useArtifact<GradeArtifact>(runId, teamId, "s4_grade.json", ready);
  if (!g.data) return null;
  const r = g.data.result;
  return (
    <div className={`card ${r.status === "DISQUALIFIED" ? "fail" : ""}`}>
      <h3>Grade <span className="muted small">{g.data.meta.prompt} × {g.data.meta.model}</span></h3>
      <p><strong style={{ fontSize: "1.4rem" }}>{r.total_score_100}</strong> / 100 {r.status === "EVALUATED" && <span className="muted">({r.sum_score_60} / 60)</span>}
        {r.status === "DISQUALIFIED" && <span className="badge FAIL"> disqualified: {r.disqualification_code}</span>}
        {r.needs_human_review && <span className="badge warnbadge"> needs human review</span>}
        {r.quality_flags.map((f) => <span key={f} className="badge warnbadge"> {f}</span>)}</p>
      {r.disqualification_reason && <p>{r.disqualification_reason}</p>}
      {r.scores && (
        <div className="tscroll"><table className="stack">
          <thead><tr><th>Criterion</th><th>Score</th><th>Feedback</th><th>Evidence</th></tr></thead>
          <tbody>{Object.entries(r.scores).map(([k, c]) => (
            <tr key={k}><td>{c.name}</td><td><strong>{c.score}</strong></td><td>{c.feedback}</td>
              <td className="small muted">{c.evidence.map((e, i) => <div key={i}>{e.slide != null && `[${e.slide}] `}{e.description}</div>)}</td></tr>))}</tbody>
        </table></div>
      )}
      <p><strong>Overall:</strong> {r.general_feedback}</p>
      {r.technical_warnings.length > 0 && <ul>{r.technical_warnings.map((w) => <li key={w} className="warn">{w}</li>)}</ul>}
      <p className="muted small">{g.data.meta.prompt} · {g.data.meta.model} · {g.data.meta.prompt_tokens}/{g.data.meta.completion_tokens} tokens · {g.data.meta.cost_rub} ₽ · {g.data.meta.attempts} attempt(s){g.data.meta.llm_skipped && " · LLM skipped (technical filter)"}</p>
    </div>
  );
}
