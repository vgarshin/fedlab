import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import type { Analytics, Bin, TopRow } from "../types";
import BarChart from "../components/BarChart";

const fmt = (n: number | null | undefined, suffix = "") => (n == null ? "–" : `${n}${suffix}`);

function Tile({ label, value, hint, hl }: { label: string; value: string; hint?: string; hl?: boolean }) {
  return <div className={`tile${hl ? " hl" : ""}`} title={hint}><div className="tile-label">{label}</div><div className="tile-value">{value}</div></div>;
}

function TopTable({ title, rows, runId }: { title: string; rows: TopRow[]; runId: number }) {
  return (
    <div className="card">
      <h3>{title}</h3>
      <div className="tscroll"><table><thead><tr><th>#</th><th>Work</th><th>Grade</th><th>Length, chars</th><th>AI prob.</th></tr></thead>
        <tbody>{rows.map((r, i) => (
          <tr key={r.id}><td>{i + 1}</td><td><Link to={`/runs/${runId}/teams/${r.id}`}>{r.name}</Link></td><td><strong>{r.grade}</strong></td>
            <td>{fmt(r.chars)}</td><td>{r.ai != null ? `${Math.round(r.ai * 100)}%` : "–"}</td></tr>))}
          {rows.length === 0 && <tr><td colSpan={5} className="muted">No graded works yet.</td></tr>}</tbody></table></div>
    </div>
  );
}

export default function AnalyticsPage() {
  const [runId, setRunId] = useState<number | undefined>(undefined);       // undefined = the latest run (server default)
  const q = useQuery({ queryKey: ["analytics", runId], queryFn: () => api.get<Analytics>(`/api/analytics${runId ? `?run_id=${runId}` : ""}`) });
  const d = q.data;
  return (
    <>
      <div className="row"><h1 style={{ margin: ".5rem 0" }}>Analytics</h1><span className="spacer" />
        {d && d.runs.length > 0 && (
          <label>Run{" "}
            <select value={d.run?.id} onChange={(e) => setRunId(Number(e.target.value))}>
              {d.runs.map((r, i) => <option key={r.id} value={r.id}>#{r.id} {r.name}{i === 0 ? " (latest)" : ""}</option>)}
            </select></label>)}
      </div>
      {q.isLoading && <p className="muted">Loading…</p>}
      {q.isError && <p className="error">{String(q.error)}</p>}
      {d && !d.run && <p className="muted">No runs yet.</p>}
      {d?.run && d.kpi && (
        <>
          <div className="tiles">
            <Tile label="Teams (works)" value={String(d.kpi.teams)} />
            <Tile label="Members" value={fmt(d.kpi.members)} hint="Rows of the Moodle worksheet whose Group is one of these teams. Empty when no worksheet was uploaded." />
            <Tile label="Graded" value={String(d.kpi.graded)} />
            <Tile label="Disqualified" value={String(d.kpi.disqualified)} hint="Failed the technical filter or removed for content (grade 0)." />
            <Tile label="Not graded yet" value={String(d.kpi.not_graded)} />
            <Tile hl label="Average grade" value={fmt(d.kpi.avg_grade)} hint="Mean of graded works (disqualified works excluded)." />
            <Tile label="Median grade" value={fmt(d.kpi.median_grade)} />
            <Tile hl label="Money spent" value={`${d.kpi.spent_rub.toFixed(2)} ₽`} hint="Grading + AI detection + OCR for this run." />
            <Tile label="Per work" value={d.kpi.cost_per_work_rub == null ? "–" : `${d.kpi.cost_per_work_rub} ₽`} />
          </div>
          <div className="charts">
            <BarChart title="Distribution of grades" subtitle="Graded works only, grade out of 100 in steps of 10" unit="works" bins={d.grade_hist ?? []}
              xLabel={(b: Bin) => `${b.from}–${b.to}`} tipTitle={(b: Bin) => `Grade ${b.from}–${b.to}`} />
            <BarChart title="Distribution of lengths" subtitle={`Characters with spaces, steps of 2 000 · ${d.over_limit ?? 0} work(s) over the ${d.char_limit} limit`} unit="works" bins={d.length_hist ?? []}
              xLabel={(b: Bin) => `${b.from / 1000}–${(b.to + 1) / 1000}k`} tipTitle={(b: Bin) => `${b.from.toLocaleString()}–${b.to.toLocaleString()} chars`}
              limitAt={d.char_limit && d.length_hist ? { index: d.char_limit / 2000, label: `limit ${d.char_limit.toLocaleString()}` } : undefined} />
          </div>
          <div className="charts">
            <TopTable title="Top 5 best works" rows={d.top ?? []} runId={d.run.id} />
            <TopTable title="5 lowest graded works" rows={d.bottom ?? []} runId={d.run.id} />
          </div>
        </>
      )}
    </>
  );
}
