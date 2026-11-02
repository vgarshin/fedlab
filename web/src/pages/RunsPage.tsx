import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import type { Me, Run } from "../types";

export default function RunsPage({ me }: { me: Me }) {
  const qc = useQueryClient();
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => api.get<Run[]>("/api/runs") });

  // useState = a value the component remembers between renders; changing it re-renders the component.
  const [name, setName] = useState("");

  // useMutation = a write operation (POST). On success we mark the cached run list as stale,
  // so React Query refetches it and the new row appears.
  const create = useMutation({
    mutationFn: () => api.post<Run>("/api/runs", { name, config: {} }),
    onSuccess: () => { setName(""); qc.invalidateQueries({ queryKey: ["runs"] }); },
  });

  return (
    <>
      <div className="row" style={{ marginBottom: ".8rem" }}>
        <h1 style={{ margin: 0 }}>Runs</h1><span className="spacer" />
        {me.role === "admin" && (
          <form className="row" onSubmit={(e) => { e.preventDefault(); if (name.trim()) create.mutate(); }}>
            <input placeholder="New run name, e.g. Autumn lab 2026" value={name} onChange={(e) => setName(e.target.value)} />
            <button className="btn primary" disabled={create.isPending || !name.trim()}>+ Create run</button>
            {create.isError && <span className="error">{String(create.error)}</span>}
          </form>
        )}
      </div>
      {runs.isLoading && <p className="muted">Loading…</p>}
      {runs.isError && <p className="error">{String(runs.error)}</p>}
      <div className="tscroll"><table className="runs">
        <thead><tr><th>#</th><th>Name</th><th>Status</th><th>Teams</th><th>Created</th></tr></thead>
        <tbody>
          {runs.data?.map((r) => (
            <tr key={r.id}>
              <td>{r.id}</td>
              <td><Link to={`/runs/${r.id}`}>{r.name}</Link></td>
              <td><span className={`badge ${r.status}`}>{r.status}</span></td>
              <td>{r.teams ?? 0}</td>
              <td className="muted">{new Date(r.created_at).toLocaleString()}</td>
            </tr>
          ))}
          {runs.data?.length === 0 && <tr><td colSpan={5} className="muted">No runs yet.</td></tr>}
        </tbody>
      </table></div>
    </>
  );
}
