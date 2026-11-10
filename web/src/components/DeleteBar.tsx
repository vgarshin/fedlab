import type { DeleteProgress } from "../types";

const STAGES = [
  { id: "input", label: "Source files (input)" },
  { id: "work", label: "Work files (intermediate)" },
  { id: "results", label: "Exported results" },
  { id: "db", label: "Database records" },
] as const;

// Run-deletion progress: the worker reports each stage over SSE ({type: "delete", stage, done, total}).
// Stages run strictly one after another, so exactly ONE bar animates at a time: finished stages show
// a ✓ with their final counts, stages that have not started yet stay dim and still. A stage with zero
// objects is instantly done (0 / 0). On failure the run is marked failed and the user can simply
// press "Delete run" again: the stages that finished list as empty and are skipped.
export default function DeleteBar({ progress }: { progress: DeleteProgress }) {
  const rows = STAGES.map((st) => {
    const p = progress.stages[st.id];
    const total = p?.total ?? 0;
    const done = p == null ? 0 : Math.min(p.done, total || p.done);
    return { ...st, p, total, done, finished: p != null && done >= total };
  });
  const active = [...rows].reverse().find((r) => r.p != null && !r.finished);
  const started = rows.some((r) => r.p != null);
  const sumDone = rows.reduce((a, r) => a + r.done, 0);
  const sumTotal = rows.reduce((a, r) => a + r.total, 0);
  return (
    <div className="card ingest-strip" role="status" aria-live="polite">
      <div className="ingest-top">
        <strong>Deleting this run…</strong>
        <span className="muted small">
          {started ? `${Math.round((sumDone / sumTotal) * 100)}% · ` : "Starting… "}
          Files and records are being removed; other runs are not affected.
        </span>
      </div>
      {rows.map((r) => {
        const isActive = r === active;
        const pct = r.finished ? 100 : isActive ? Math.round((r.done / r.total) * 100) : 0;
        return (
          <div className={`delete-row ${!isActive && !r.finished ? "pending" : ""}`} key={r.id}>
            <span className="delete-label small">{r.finished ? "✓" : isActive ? "…" : "·"} {r.label}</span>
            <div className="bar"><div style={{ width: `${pct}%` }} /></div>
            <span className="muted small nowrap">{r.total > 0 ? `${r.done} / ${r.total}` : r.finished ? "empty" : ""}</span>
          </div>
        );
      })}
      {progress.error && <p className="error small" style={{ marginTop: ".5rem" }}>{progress.error} — press “Delete run” again to retry; already deleted parts are skipped.</p>}
    </div>
  );
}
