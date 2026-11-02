import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "../api";
import Switch from "../components/Switch";
import type { PlaygroundOptions, PlaygroundOut, Run, TeamRow } from "../types";

// Trigger a browser download of text generated on the client: no server round trip needed.
function saveFile(name: string, text: string, mime = "text/plain;charset=utf-8") {
  const url = URL.createObjectURL(new Blob([text], { type: mime }));
  const a = document.createElement("a");
  a.href = url; a.download = name; a.click();
  URL.revokeObjectURL(url);
}

interface Scores { total_score_100?: number; sum_score_60?: number; status?: string; general_feedback?: string; disqualification_code?: string;
  scores?: Record<string, { name: string; score: number; feedback: string }> | null }

export default function PlaygroundPage() {
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => api.get<Run[]>("/api/runs") });
  const opts = useQuery({ queryKey: ["playground-options"], queryFn: () => api.get<PlaygroundOptions>("/api/playground/options") });
  const [runId, setRunId] = useState("");
  const [teamId, setTeamId] = useState("");
  const [promptId, setPromptId] = useState("");
  const [modelId, setModelId] = useState("");
  const [fresh, setFresh] = useState(false);
  const teams = useQuery({ queryKey: ["teams", runId], queryFn: () => api.get<TeamRow[]>(`/api/runs/${runId}/teams`), enabled: !!runId });

  // Defaults: latest run (the API lists newest first), first work, the prompt+model chosen on the Settings page.
  useEffect(() => { if (!runId && runs.data?.length) setRunId(String(runs.data[0].id)); }, [runs.data, runId]);
  useEffect(() => { setTeamId(teams.data?.length ? String(teams.data[0].id) : ""); }, [teams.data]);
  useEffect(() => {
    if (promptId || !opts.data) return;
    const pick = opts.data.prompts.find((p) => p.name === opts.data!.defaults.grade_prompt && p.status === "published") ?? opts.data.prompts[0];
    if (pick) setPromptId(String(pick.id));
  }, [opts.data, promptId]);
  useEffect(() => { if (!modelId && opts.data) setModelId(opts.data.models.find((m) => m.id === opts.data!.defaults.grade_model)?.id ?? opts.data.models[0]?.id ?? ""); }, [opts.data, modelId]);

  const test = useMutation({
    mutationFn: () => api.post<PlaygroundOut>("/api/playground/run", { run_id: Number(runId), team_id: Number(teamId), prompt_id: Number(promptId), model_id: modelId, fresh }),
  });
  const team = teams.data?.find((t) => String(t.id) === teamId);
  const noStage2 = !!team && team.precheck_status == null;
  const r = test.data;
  const parsed = (r?.parsed ?? null) as Scores | null;
  const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
  const base = r ? `playground_${r.team}_${r.prompt}_${r.model}`.replace(/[^\w.\-]+/g, "_") + `_${stamp}` : "";

  return (
    <>
      <h1>Playground</h1>
      <p className="muted">Try any prompt version with any enabled model on one work of a run. Nothing is saved in the run; the call is billed to the chosen run (shown under its spending) and stops once the run’s budget cap is reached.</p>
      <div className="card sticky-bar">
        <div className="row">
          <label>Run<br /><select value={runId} onChange={(e) => setRunId(e.target.value)}>
            {runs.data?.map((x, i) => <option key={x.id} value={x.id}>#{x.id} {x.name}{i === 0 ? " (latest)" : ""}</option>)}</select></label>
          <label>Work<br /><select value={teamId} onChange={(e) => setTeamId(e.target.value)}>
            {teams.data?.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select></label>
        </div>
        <div className="row" style={{ marginTop: ".6rem" }}>
          <label>Prompt<br /><select value={promptId} onChange={(e) => setPromptId(e.target.value)}>
            {opts.data?.prompts.map((p) => <option key={p.id} value={p.id}>{p.name} v{p.version} ({p.status}, ~{p.est_tokens} tok)</option>)}</select></label>
          <label>Model<br /><select value={modelId} onChange={(e) => setModelId(e.target.value)}>
            {opts.data?.models.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}</select></label>
          <Switch checked={fresh} onChange={setFresh} label="fresh call (skip the cache, costs money)" title="Without it, an identical earlier request is answered from the cache: the same text, 0 ₽." />
          <button className="btn primary" disabled={!runId || !teamId || !promptId || !modelId || test.isPending || noStage2} onClick={() => test.mutate()}>{test.isPending ? "Running…" : "▶ Run experiment"}</button>
        </div>
        {noStage2 && <p className="warn small">Stage 2 (extraction) has not run for this work yet — start it on the run page first.</p>}
        {test.isError && <p className="error">{String(test.error)}</p>}
      </div>


      <div className="split pg-split">
        <div>
          <h3>Source file{team ? ` · ${team.name}` : ""}</h3>
          {/* same-origin PDF endpoint, shown by the browser's built-in viewer (as on a work's page) */}
          {team?.is_pdf ? <iframe title="source pdf" className="pdf" src={`/api/runs/${runId}/teams/${teamId}/file`} />
            : <p className="muted">{team ? `Not a PDF file: ${team.filename}` : "Choose a run and a work."}</p>}
        </div>
        <div>
          <h3>Model answer {r && <span className="muted small">· {r.prompt} × {r.model}</span>}</h3>
          <textarea className="pg-answer" readOnly value={test.isPending ? "Waiting for the model…" : r ? r.raw : ""}
            placeholder="The raw text returned by the model appears here after you run the experiment." />
        </div>
      </div>

      {r && (
        <div className="card">
          <div className="row"><h3 style={{ margin: 0 }}>Result</h3><span className="spacer" />
            <button className="btn" onClick={() => saveFile(`${base}_result.json`, JSON.stringify(r.parsed ?? { error: r.error }, null, 2), "application/json")}>Save result (.json)</button>
            <button className="btn" onClick={() => saveFile(`${base}_answer.txt`, r.raw)}>Save raw answer (.txt)</button>
            <button className="btn" onClick={() => saveFile(`${base}_report.json`, JSON.stringify({ run_id: Number(runId), team: r.team, prompt: r.prompt, model: r.model, fresh_call: fresh,
              cache_hit: r.cache_hit, prompt_tokens: r.prompt_tokens, completion_tokens: r.completion_tokens, cost_rub: r.cost_rub, latency_ms: r.latency_ms, validation_error: r.error,
              result: r.parsed, raw_answer: r.raw, input_sent: r.input_full }, null, 2), "application/json")}>Save full report (.json)</button>
          </div>
          <p className="small muted">{r.team} · {r.prompt} × {r.model} · {r.prompt_tokens}/{r.completion_tokens} tokens · {r.cost_rub.toFixed(4)} ₽ · {(r.latency_ms / 1000).toFixed(1)} s{r.cache_hit && " · from cache"}</p>
          {r.error && <p className="error">Rejected by the format check: {r.error}</p>}
          {parsed?.total_score_100 != null && (
            <>
              <p><strong style={{ fontSize: "1.4rem" }}>{parsed.total_score_100}</strong> / 100 {parsed.status === "EVALUATED" && <span className="muted">({parsed.sum_score_60} / 60)</span>}
                {parsed.status === "DISQUALIFIED" && <span className="badge FAIL"> disqualified: {parsed.disqualification_code}</span>}</p>
              {parsed.scores && <div className="tscroll"><table className="stack"><thead><tr><th>Criterion</th><th>Score</th><th>Feedback</th></tr></thead>
                <tbody>{Object.entries(parsed.scores).map(([k, c]) => <tr key={k}><td>{c.name}</td><td><strong>{c.score}</strong></td><td>{c.feedback}</td></tr>)}</tbody></table></div>}
              <p><strong>Overall:</strong> {parsed.general_feedback}</p>
            </>)}
          <details><summary>Parsed result (JSON)</summary><pre>{JSON.stringify(r.parsed, null, 2)}</pre></details>
          <details><summary>Exact input sent to the model (user message)</summary><pre>{r.input_full}</pre></details>
        </div>)}
    </>
  );
}
