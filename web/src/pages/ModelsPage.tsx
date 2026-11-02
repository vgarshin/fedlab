import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import Switch from "../components/Switch";
import type { Me, ModelRow } from "../types";

// The registry of LLMs. Prices are data: when a provider changes them, edit here, not in code.
export default function ModelsPage({ me }: { me: Me }) {
  const qc = useQueryClient();
  const models = useQuery({ queryKey: ["models"], queryFn: () => api.get<ModelRow[]>("/api/models") });
  const patch = useMutation({
    mutationFn: (v: { id: string } & Partial<ModelRow>) => api.patch<ModelRow>(`/api/models/${v.id}`, v),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["models"] }),
  });
  const admin = me.role === "admin";
  return (
    <>
      <h1>Models</h1>
      <p className="muted">Switch models on or off here. Which model is used for grading and for AI detection is chosen on the Settings page. Prices are rubles per 1M tokens (read-only).</p>
      <div className="tscroll"><table>
        <thead><tr><th>Model</th><th>Provider</th><th>₽ in / 1M</th><th>₽ cached in / 1M</th><th>₽ out / 1M</th><th>Parallel</th><th>Reasoning</th><th>Prices checked</th><th>Enabled</th></tr></thead>
        <tbody>{models.data?.map((m) => (
          <tr key={m.id}>
            <td><strong>{m.label || m.id}</strong><div className="muted small">{m.id} → {m.model}</div></td>
            <td>{m.provider}</td>
            <td>{m.price_in_per_1m}</td>
            <td>{m.price_cached_per_1m || "= in"}</td>
            <td>{m.price_out_per_1m}</td>
            <td>{m.max_concurrency}</td>
            <td>{m.reasoning_effort || "default"}</td>
            <td className="muted small">{m.prices_checked || "–"}</td>
            <td><Switch checked={m.enabled} disabled={!admin} onChange={(on) => patch.mutate({ id: m.id, enabled: on })} /></td>
          </tr>))}</tbody>
      </table></div>
      {patch.isError && <p className="error">{String(patch.error)}</p>}
    </>
  );
}
