import type { IngestEvent } from "../types";

const mb = (b: number) => (b / 1048576 >= 1 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`);

// Stage 1 (ingest) progress: the worker publishes counters over SSE every 2 s while a batch is
// unpacked into the run. The label names the first phase that is still unfinished; until the first
// tick arrives (or when nothing is measurable yet) the bar runs indeterminate so it never looks stuck.
export default function IngestBar({ progress }: { progress: IngestEvent | null }) {
  const p = progress;
  let label = "Preparing the batch…";
  if (p && p.bytes_total > 0 && p.bytes_done < p.bytes_total)
    label = `Reading the Moodle export from storage — ${mb(p.bytes_done)} of ${mb(p.bytes_total)}`;
  else if (p && p.unpack_total > 0 && p.unpack_done < p.unpack_total)
    label = `Unpacking — ${p.unpack_done} of ${p.unpack_total} files`;
  else if (p && p.register_total > 0 && p.register_done < p.register_total)
    label = `Registering teams — ${p.register_done} of ${p.register_total}`;
  else if (p) label = "Finishing up…";
  const pct = p?.percent ?? null;
  return (
    <div className="card ingest-strip" role="status" aria-live="polite">
      <div className="ingest-top">
        <strong>Ingesting files</strong>
        <span className="muted small">{label}</span>
        <span className="spacer" />
        <span className="muted small nowrap">{pct != null ? `${pct}%` : ""}</span>
      </div>
      <div className={`bar tall ${pct == null ? "indeterminate" : ""}`}>
        <div style={pct != null ? { width: `${pct}%` } : undefined} />
      </div>
    </div>
  );
}
