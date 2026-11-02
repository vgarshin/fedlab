import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import type { ApiKeyRow, OpenApiDoc, OpenApiOp, SchemaObj } from "../types";

const BASE = `${window.location.origin}`;
const METHOD_ORDER = ["get", "post", "put", "patch", "delete"];

// ---- tiny OpenAPI helpers -------------------------------------------------------------------------------
function resolve(doc: OpenApiDoc, s?: SchemaObj, depth = 0): SchemaObj {
  if (!s) return {};
  if (s.$ref && depth < 6) return resolve(doc, doc.components?.schemas?.[s.$ref.split("/").pop()!], depth + 1);
  return s;
}
function typeOf(doc: OpenApiDoc, s?: SchemaObj): string {
  const r = resolve(doc, s);
  if (r.anyOf) return r.anyOf.map((x) => typeOf(doc, x)).filter((t) => t !== "null").join(" | ") || "null";
  if (r.enum) return r.enum.map((e) => JSON.stringify(e)).join(" | ");
  if (r.type === "array") return `${typeOf(doc, r.items)}[]`;
  return r.type ?? "object";
}
function example(doc: OpenApiDoc, s?: SchemaObj, depth = 0): unknown {
  const r = resolve(doc, s);
  if (r.default !== undefined) return r.default;
  if (r.anyOf) return example(doc, r.anyOf.find((x) => x.type !== "null") ?? r.anyOf[0], depth + 1);
  if (r.enum) return r.enum[0];
  if (r.type === "object" || r.properties) {
    if (depth > 3) return {};
    return Object.fromEntries(Object.entries(r.properties ?? {}).filter(([k]) => (r.required ?? []).includes(k) || depth === 0).map(([k, v]) => [k, example(doc, v, depth + 1)]));
  }
  return { string: "string", integer: 1, number: 1.0, boolean: true, array: [] }[r.type ?? "string"] ?? null;
}
function fields(doc: OpenApiDoc, s?: SchemaObj) {
  const r = resolve(doc, s);
  return Object.entries(r.properties ?? {}).map(([name, v]) => {
    const rv = resolve(doc, v);
    return { name, type: typeOf(doc, v), required: (r.required ?? []).includes(name), def: rv.default, desc: rv.description };
  });
}

function ageText(d: string | null) { return d ? new Date(d).toLocaleString() : "–"; }

// ---- API keys -------------------------------------------------------------------------------------------
function Keys() {
  const qc = useQueryClient();
  const keys = useQuery({ queryKey: ["apikeys"], queryFn: () => api.get<ApiKeyRow[]>("/api/meta/keys") });
  const [name, setName] = useState("");
  const [role, setRole] = useState<"viewer" | "admin">("viewer");
  const [days, setDays] = useState("90");
  const [fresh, setFresh] = useState<(ApiKeyRow & { key: string }) | null>(null);
  const [copied, setCopied] = useState(false);
  const create = useMutation({
    mutationFn: () => api.post<ApiKeyRow & { key: string }>("/api/meta/keys", { name, role, expires_days: days ? Number(days) : null }),
    onSuccess: (k) => { setFresh(k); setCopied(false); setName(""); qc.invalidateQueries({ queryKey: ["apikeys"] }); },
  });
  const revoke = useMutation({ mutationFn: (id: number) => api.post(`/api/meta/keys/${id}/revoke`), onSuccess: () => qc.invalidateQueries({ queryKey: ["apikeys"] }) });
  const status = (k: ApiKeyRow) => k.revoked_at ? "revoked" : k.expires_at && new Date(k.expires_at) < new Date() ? "expired" : "active";
  return (
    <div className="card">
      <h3>API keys for outside services</h3>
      <form className="row" onSubmit={(e) => { e.preventDefault(); if (name.trim().length >= 2) create.mutate(); }}>
        <input placeholder="Key name, e.g. moodle-sync" value={name} onChange={(e) => setName(e.target.value)} />
        <select value={role} onChange={(e) => setRole(e.target.value as "viewer" | "admin")}>
          <option value="viewer">viewer · read-only</option><option value="admin">admin · full access (except keys)</option></select>
        <select value={days} onChange={(e) => setDays(e.target.value)}>
          <option value="30">expires in 30 days</option><option value="90">90 days</option><option value="365">1 year</option><option value="">never expires</option></select>
        <button className="btn primary" disabled={create.isPending || name.trim().length < 2}>Create key</button>
      </form>
      {create.isError && <p className="error">{String(create.error)}</p>}
      {fresh && (
        <div className="card warncard">
          <p><strong>Copy this key now — it is shown only once.</strong> Only a hash is stored; if you lose it, revoke it and create a new one.</p>
          <div className="row"><code className="keybox">{fresh.key}</code>
            <button className="btn" onClick={() => { navigator.clipboard?.writeText(fresh.key); setCopied(true); }}>{copied ? "Copied ✓" : "Copy"}</button>
            <button className="link" onClick={() => setFresh(null)}>hide</button></div>
        </div>)}
      <div className="tscroll"><table>
        <thead><tr><th>Name</th><th>Key</th><th>Role</th><th>Created</th><th>Expires</th><th>Last used</th><th>Status</th><th></th></tr></thead>
        <tbody>{keys.data?.map((k) => (
          <tr key={k.id}><td>{k.name}</td><td><code>{k.prefix}…</code></td><td>{k.role}</td><td className="small">{ageText(k.created_at)}</td>
            <td className="small">{k.expires_at ? ageText(k.expires_at) : "never"}</td><td className="small">{ageText(k.last_used_at)}</td>
            <td><span className={`badge ${status(k) === "active" ? "done" : "FAIL"}`}>{status(k)}</span></td>
            <td>{status(k) === "active" && <button className="link" onClick={() => { if (window.confirm(`Revoke key “${k.name}”? Services using it stop working immediately.`)) revoke.mutate(k.id); }}>revoke</button>}</td></tr>))}
          {keys.data?.length === 0 && <tr><td colSpan={8} className="muted">No keys yet.</td></tr>}</tbody>
      </table></div>
    </div>
  );
}

// ---- reference ------------------------------------------------------------------------------------------
function Operation({ doc, method, path, op }: { doc: OpenApiDoc; method: string; path: string; op: OpenApiOp }) {
  const [open, setOpen] = useState(false);
  const body = op.requestBody?.content["application/json"]?.schema;
  const multipart = op.requestBody?.content["multipart/form-data"]?.schema;
  const bodyExample = body ? JSON.stringify(example(doc, body)) : "";
  const urlPath = path.replace(/\{(\w+)\}/g, "<$1>");
  const curl = [`curl -X ${method.toUpperCase()} "${BASE}${urlPath}"`, `  -H "Authorization: Bearer $FEDLAB_KEY"`,
    ...(body ? [`  -H "Content-Type: application/json"`, `  -d '${bodyExample}'`] : []), ...(multipart ? [`  -F submissions=@moodle_export.zip`] : [])].join(" \\\n");
  const role = op["x-role"];
  return (
    <div className="op">
      <button className="op-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className={`method m-${method}`}>{method.toUpperCase()}</span><code className="op-path">{path}</code>
        <span className="op-sum">{op.summary}</span>
        <span className={`badge ${role.startsWith("admin") ? "warnbadge" : role === "public" ? "" : "done"}`}>{role}</span>
      </button>
      {open && (
        <div className="op-body">
          {op.description && <p style={{ whiteSpace: "pre-wrap" }}>{op.description}</p>}
          {op.parameters && op.parameters.length > 0 && (<><h4>Parameters</h4><div className="tscroll"><table><thead><tr><th>Name</th><th>In</th><th>Type</th><th>Required</th></tr></thead>
            <tbody>{op.parameters.map((p) => <tr key={p.name + p.in}><td><code>{p.name}</code></td><td>{p.in}</td><td>{typeOf(doc, p.schema)}</td><td>{p.required ? "yes" : "no"}</td></tr>)}</tbody></table></div></>)}
          {body && (<><h4>JSON body</h4><div className="tscroll"><table><thead><tr><th>Field</th><th>Type</th><th>Required</th><th>Default</th></tr></thead>
            <tbody>{fields(doc, body).map((f) => <tr key={f.name}><td><code>{f.name}</code></td><td>{f.type}</td><td>{f.required ? "yes" : "no"}</td><td>{f.def === undefined ? "" : JSON.stringify(f.def)}</td></tr>)}</tbody></table></div></>)}
          {multipart && (<><h4>Multipart form</h4><div className="tscroll"><table><thead><tr><th>Field</th><th>Type</th><th>Required</th></tr></thead>
            <tbody>{fields(doc, multipart).map((f) => <tr key={f.name}><td><code>{f.name}</code></td><td>{f.type}</td><td>{f.required ? "yes" : "no"}</td></tr>)}</tbody></table></div></>)}
          <h4>Responses</h4>
          <ul className="small">{Object.entries(op.responses ?? {}).filter(([c]) => c !== "422").map(([c, r]) => <li key={c}><code>{c}</code> {r.description}</li>)}
            <li><code>401</code> not signed in / bad key · <code>403</code> role too low · <code>422</code> invalid input</li></ul>
          <h4>Example</h4>
          <pre>{curl}</pre>
        </div>)}
    </div>
  );
}

function Reference() {
  const q = useQuery({ queryKey: ["openapi"], queryFn: () => api.get<OpenApiDoc>("/api/meta/openapi") });
  const [filter, setFilter] = useState("");
  if (q.isLoading) return <p className="muted">Loading…</p>;
  if (q.isError || !q.data) return <p className="error">{String(q.error)}</p>;
  const doc = q.data;
  const groups: Record<string, { path: string; method: string; op: OpenApiOp }[]> = {};
  for (const [path, ops] of Object.entries(doc.paths))
    for (const [method, op] of Object.entries(ops)) {
      if (!METHOD_ORDER.includes(method)) continue;
      const hay = `${path} ${op.summary ?? ""} ${op.description ?? ""} ${method}`.toLowerCase();
      if (filter && !hay.includes(filter.toLowerCase())) continue;
      (groups[op.tags?.[0] ?? "other"] ??= []).push({ path, method, op });
    }
  return (
    <>
      <div className="row"><h2 style={{ margin: ".5rem 0" }}>Reference</h2><span className="spacer" />
        <input placeholder="Filter: path, text, method…" value={filter} onChange={(e) => setFilter(e.target.value)} /></div>
      <p className="muted small">Generated from the running service ({doc.info.title} {doc.info.version}); the role on each line is what the endpoint requires. Click a line for details.</p>
      {Object.entries(groups).sort().map(([tag, ops]) => (
        <details key={tag} className="card" open={!!filter || tag === "runs"}>
          <summary><strong>{tag}</strong> <span className="muted small">({ops.length})</span></summary>
          {ops.sort((a, b) => a.path.localeCompare(b.path) || METHOD_ORDER.indexOf(a.method) - METHOD_ORDER.indexOf(b.method))
            .map((o) => <Operation key={o.method + o.path} doc={doc} {...o} />)}
        </details>))}
    </>
  );
}

export default function ApiPage() {
  return (
    <>
      <h1>API</h1>
      <div className="card">
        <h3>How to call it</h3>
        <ul className="small">
          <li>Base address: <code>{BASE}/api/…</code>. JSON in, JSON out.</li>
          <li>Send a key in a header: <code>Authorization: Bearer fl_…</code> (or <code>X-API-Key: fl_…</code>). No cookies and no extra headers are needed.</li>
          <li><strong>viewer</strong> keys can read runs, works, results and analytics and download files; <strong>admin</strong> keys can also upload, start stages and rebuild exports. No key can create or revoke keys, or open Prompts, Settings, Models and Users in the browser.</li>
          <li>Typical flow for a service: <code>POST /api/runs</code> → upload or <code>use-incoming</code> → <code>POST /api/runs/&lt;id&gt;/run-all</code> → poll <code>GET /api/runs/&lt;id&gt;</code> until its stages are done → <code>GET /api/runs/&lt;id&gt;/export/zip</code>.</li>
          <li>Limits: about 30 requests per second per address; uploads up to 500 MB.</li>
        </ul>
        <pre>{`export FEDLAB_KEY=fl_...\ncurl -H "Authorization: Bearer $FEDLAB_KEY" ${BASE}/api/runs`}</pre>
      </div>
      <Keys />
      <Reference />
    </>
  );
}
