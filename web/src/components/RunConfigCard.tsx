import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import type { CostInfo, Estimate, Run } from "../types";

// Information only (collapsed by default): what this run uses and what it has cost. Everything is changed elsewhere:
// models/prompts, AI-detection switch and budget cap on Settings; OCR in the Stage 2 block.
export default function RunConfigCard({ run, admin }: { run: Run; admin: boolean }) {
  const est = useQuery({ queryKey: ["estimate", run.id, run.uses], queryFn: () => api.get<Estimate>(`/api/runs/${run.id}/estimate`) });
  const cost = useQuery({ queryKey: ["cost", String(run.id)], queryFn: () => api.get<CostInfo>(`/api/runs/${run.id}/cost`), refetchInterval: 5000 });
  const u = run.uses;
  const line = (label: string, s?: { prompt: string; model: string; ok: boolean; problem?: string }) => s && (
    <div><strong>{label}:</strong> {s.prompt} × {s.model} {!s.ok && <span className="error"> — {s.problem}</span>}</div>);
  return (
    <details className="card">
      <summary><strong>Configuration &amp; cost</strong>{" "}
        <span className="muted small">{cost.data ? `spent ${cost.data.spent_rub.toFixed(2)} of ${cost.data.cap_rub} ₽` : ""}</span></summary>
      <div className="small">
        {line("Grading", u?.grade)}
        {u?.detect_enabled ? line("AI detection", u?.detect) : <div><strong>AI detection:</strong> <span className="muted">off</span></div>}
        <div><strong>OCR for scans:</strong> {run.config.ocr_enabled ? "on" : "off"} <span className="muted">(switch in the Stage 2 block)</span></div>
        {admin && <div className="muted">Models, prompts, the AI-detection switch and the budget cap are on the <Link to="/settings">Settings</Link> page.</div>}
        <div style={{ marginTop: ".5rem" }}><strong>Spent:</strong> {cost.data ? `${cost.data.spent_rub.toFixed(2)} ₽ of the ${cost.data.cap_rub} ₽ budget cap · ${cost.data.calls} paid calls · ${cost.data.cache_hits} cache hits` : "…"}
          {est.data && <span className="muted" title={est.data.note}> · estimate for a full pass ≈ {est.data.total_rub} ₽ ({est.data.teams_eligible} works)</span>}</div>
      </div>
    </details>
  );
}
