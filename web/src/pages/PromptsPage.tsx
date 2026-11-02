import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import type { Me, PromptRow } from "../types";

// Line diff (LCS) between two prompt versions, small enough to read in one go.
function diffLines(a: string, b: string): { t: " " | "+" | "-"; s: string }[] {
  const x = a.split("\n"), y = b.split("\n");
  const dp = Array.from({ length: x.length + 1 }, () => new Array<number>(y.length + 1).fill(0));
  for (let i = x.length - 1; i >= 0; i--) for (let j = y.length - 1; j >= 0; j--)
    dp[i][j] = x[i] === y[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out: { t: " " | "+" | "-"; s: string }[] = [];
  let i = 0, j = 0;
  while (i < x.length && j < y.length) {
    if (x[i] === y[j]) { out.push({ t: " ", s: x[i] }); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) out.push({ t: "-", s: x[i++] });
    else out.push({ t: "+", s: y[j++] });
  }
  while (i < x.length) out.push({ t: "-", s: x[i++] });
  while (j < y.length) out.push({ t: "+", s: y[j++] });
  return out;
}

export default function PromptsPage({ me }: { me: Me }) {
  const qc = useQueryClient();
  const admin = me.role === "admin";
  const prompts = useQuery({ queryKey: ["prompts"], queryFn: () => api.get<PromptRow[]>("/api/prompts") });
  const [selId, setSelId] = useState<number | null>(null);
  const sel = useQuery({ queryKey: ["prompt", selId], queryFn: () => api.get<PromptRow>(`/api/prompts/${selId}`), enabled: selId != null });
  const [text, setText] = useState<string | null>(null);   // null = viewing, string = editing
  const [note, setNote] = useState("");
  const [showDiff, setShowDiff] = useState(false);

  const create = useMutation({
    mutationFn: () => api.post<PromptRow>("/api/prompts", { name: sel.data!.name, text, note }),
    onSuccess: (p) => { setText(null); setNote(""); setSelId(p.id); qc.invalidateQueries({ queryKey: ["prompts"] }); },
  });
  const publish = useMutation({
    mutationFn: (id: number) => api.post(`/api/prompts/${id}/publish`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["prompts"] }); qc.invalidateQueries({ queryKey: ["prompt", selId] }); },
  });

  const names = [...new Set(prompts.data?.map((p) => p.name))];
  const prev = prompts.data?.filter((p) => p.name === sel.data?.name && p.version < (sel.data?.version ?? 0))[0];
  const prevText = useQuery({ queryKey: ["prompt", prev?.id], queryFn: () => api.get<PromptRow>(`/api/prompts/${prev!.id}`), enabled: !!prev && showDiff });

  return (
    <>
      <h1>Prompts</h1>
      <p className="muted">Prompts are versioned and immutable: editing saves a NEW version (draft). Runs pin the exact version they use, so old results stay reproducible.</p>
      <div className="split" style={{ gridTemplateColumns: "1fr 2fr" }}>
        <div>
          {names.map((n) => (
            <div className="card" key={n}>
              <strong>{n}</strong>
              {prompts.data?.filter((p) => p.name === n).map((p) => (
                <div key={p.id}><button className="link" onClick={() => { setSelId(p.id); setText(null); setShowDiff(false); }}
                  style={{ fontWeight: p.id === selId ? 700 : 400 }}>v{p.version}</button>{" "}
                  <span className={`badge ${p.status === "published" ? "done" : ""}`}>{p.status}</span>{" "}
                  <span className="muted small">{p.chars} chars · ~{p.est_tokens} tok · {p.note}</span></div>
              ))}
            </div>))}
        </div>
        <div>
          {sel.data && (
            <>
              <h3>{sel.data.name} v{sel.data.version} <span className="muted small">hash {sel.data.hash}</span></h3>
              <div className="row">
                {admin && text === null && <button className="btn" onClick={() => setText(sel.data!.text ?? "")}>Edit as new version</button>}
                {admin && sel.data.status === "draft" && <button className="btn primary" onClick={() => publish.mutate(sel.data!.id)}>Publish</button>}
                {prev && <button className="btn" onClick={() => setShowDiff(!showDiff)}>{showDiff ? "Hide" : "Show"} diff vs v{prev.version}</button>}
              </div>
              {text !== null ? (
                <>
                  <textarea className="prompt-edit" value={text} onChange={(e) => setText(e.target.value)} />
                  <div className="row">
                    <input placeholder="What changed? (note)" value={note} onChange={(e) => setNote(e.target.value)} style={{ flex: 1 }} />
                    <button className="btn primary" disabled={create.isPending || text.length < 20} onClick={() => create.mutate()}>Save as v{(prompts.data?.filter((p) => p.name === sel.data!.name).length ?? 0) + 1}</button>
                    <button className="btn" onClick={() => setText(null)}>Cancel</button>
                  </div>
                  {create.isError && <p className="error">{String(create.error)}</p>}
                </>
              ) : showDiff && prevText.data ? (
                <pre className="diff">{diffLines(prevText.data.text ?? "", sel.data.text ?? "").filter((l, i, a) => l.t !== " " || a.slice(Math.max(0, i - 2), i + 3).some((n) => n.t !== " ")).map((l, i) => <div key={i} className={`d${l.t === "+" ? "add" : l.t === "-" ? "del" : "ctx"}`}>{l.t} {l.s}</div>)}</pre>
              ) : <pre className="prompt-view">{sel.data.text}</pre>}
              <p className="muted small">To test this prompt on a work, open the <Link to="/playground">Playground</Link>.</p>
            </>
          )}
        </div>
      </div>
    </>
  );
}
