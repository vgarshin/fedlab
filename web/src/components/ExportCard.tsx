import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import type { ExportInfo } from "../types";

function useExport(runId: string) {
  return useQuery({ queryKey: ["export", runId], queryFn: () => api.get<ExportInfo>(`/api/runs/${runId}/export`), refetchInterval: (q) => (q.state.data?.files.length ? false : 8000) });
}

// Buttons for the run page header: (re)build the export, download everything as a zip.
export function ExportActions({ runId, admin }: { runId: string; admin: boolean }) {
  const qc = useQueryClient();
  const info = useExport(runId);
  const build = useMutation({
    mutationFn: () => api.post(`/api/runs/${runId}/export`),
    onSuccess: () => { setTimeout(() => qc.invalidateQueries({ queryKey: ["export", runId] }), 4000); setTimeout(() => qc.invalidateQueries({ queryKey: ["export", runId] }), 12000); },
  });
  const e = info.data?.last_export;
  return (
    <>
      {admin && <button className="btn" disabled={build.isPending} onClick={() => build.mutate()} title="Re-creates all result files from the stored grades (free, no model calls)">{e ? "Rebuild export" : "Build export"}</button>}
      {info.data && info.data.files.length > 0 && <a className="btn" href={`/api/runs/${runId}/export/zip`}>⬇ Results (zip)</a>}
      {build.isError && <span className="error small">{String(build.error)}</span>}
    </>
  );
}

// Collapsible list of the run-level result files.
export default function ExportCard({ runId }: { runId: string }) {
  const d = useExport(runId).data;
  const e = d?.last_export;
  const top = d?.files.filter((f) => !f.path.includes("/")) ?? [];
  return (
    <details className="card">
      <summary><strong>Export</strong>{" "}
        <span className="muted small">{e ? `${new Date(e.exported_at).toLocaleString()} · ${e.teams_exported} works · ${top.length} run-level files` : "not built yet"}</span></summary>
      {!e && <p className="muted small">Builds the result tree in <code>{d?.bucket}/{d?.prefix}</code> exactly like the spring result bucket: Moodle worksheet CSV with Grade and Feedback filled, per-team feedback files, aggregate JSON, jury table.</p>}
      {e && (
        <>
          <p className="small">worksheet: {e.worksheet ? `${e.worksheet.updated} of ${e.worksheet.rows} rows filled (${e.worksheet.delimiter}-separated)` : "none uploaded"}
            {e.teams_missing_grade.length > 0 && <span className="warn"> · not graded yet: {e.teams_missing_grade.join(", ")}</span>}</p>
          {Object.keys(e.tie_groups).length > 0 && <p className="small warn">Equal totals (ties are decided by the jury, not by the system): {Object.entries(e.tie_groups).map(([t, n]) => `${t} points × ${n}`).join(" · ")}</p>}
          {e.needs_review.length > 0 && <p className="small warn">Needs human review: {e.needs_review.join(", ")}</p>}
          <ul className="small cols2">
            {top.map((f) => <li key={f.path}><a href={`/api/runs/${runId}/export/file?path=${encodeURIComponent(f.path)}`}>{f.path}</a> <span className="muted">{(f.bytes / 1024).toFixed(1)} KB</span></li>)}
          </ul>
          <p className="muted small">Per-work files are listed on each work's page.</p>
        </>
      )}
    </details>
  );
}
