import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import type { BatchInfo, BatchList, Reserved, Run, SettingsData } from "../types";
import FilePick from "./FilePick";

const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{2,59}$/;
const pad = (n: number) => String(n).padStart(2, "0");
// Default batch name in YOUR local time: YYYYMMDD-HHMM (no colon: awkward in file names and URLs).
export const defaultBatchName = () => { const d = new Date(); return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}`; };
const mb = (b: number) => (b / 1048576 >= 1 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`);

type Mode = "upload" | "cli" | "existing";

// Stage 1 · where the files of this run come from. Every source is a BATCH: a folder incoming/<name>/ that is written ONCE.
export default function SourceFiles({ run, onChange }: { run: Run; onChange: () => void }) {
  const qc = useQueryClient();
  const ready = run.status === "ready";
  const [mode, setMode] = useState<Mode>("upload");
  const [name, setName] = useState(defaultBatchName());
  const [zip, setZip] = useState<File | null>(null);
  const [csv, setCsv] = useState<File | null>(null);
  const [reserved, setReserved] = useState<Reserved | null>(null);
  const [copied, setCopied] = useState("");
  const nameOk = NAME_RE.test(name);
  const used = run.source?.batches ?? (run.source?.prefix ? [run.source.prefix.replace(/\/$/, "").split("/").pop()!] : []);

  const batches = useQuery({ queryKey: ["batches"], queryFn: () => api.get<BatchList>("/api/batches"), enabled: mode === "existing" });
  // The upload limit lives in the app config; the API exposes it so the UI can reject
  // an oversized zip BEFORE the browser wastes time sending hundreds of megabytes.
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api.get<SettingsData>("/api/settings") });
  const limitBytes = (settings.data?.processing.max_upload_mb ?? 0) << 20;
  const zipTooBig = !!zip && limitBytes > 0 && zip.size > limitBytes;
  const refreshAll = () => { qc.invalidateQueries({ queryKey: ["batches"] }); onChange(); };

  const upload = useMutation({
    mutationFn: () => {
      const f = new FormData();                       // multipart/form-data, as a browser file input sends it
      f.append("batch_name", name); f.append("submissions", zip!); if (csv) f.append("worksheet", csv);
      return api.upload<{ batch: string }>(`/api/runs/${run.id}/upload`, f);
    },
    onSuccess: () => { setZip(null); setCsv(null); setName(defaultBatchName()); refreshAll(); },
  });
  const reserve = useMutation({ mutationFn: () => api.post<Reserved>("/api/batches", { name }), onSuccess: (r) => { setReserved(r); qc.invalidateQueries({ queryKey: ["batches"] }); } });
  const use = useMutation({ mutationFn: (b: string) => api.post(`/api/runs/${run.id}/use-incoming`, { batch: b }), onSuccess: refreshAll });
  const del = useMutation({ mutationFn: (b: string) => api.del(`/api/batches/${b}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["batches"] }) });
  const reingest = useMutation({ mutationFn: () => api.post(`/api/runs/${run.id}/reingest`), onSuccess: refreshAll });
  const copy = (t: string) => { navigator.clipboard?.writeText(t); setCopied(t); };

  const nameField = (
    <label>Batch name <span className="muted small">(new, never reused)</span><br />
      <input value={name} onChange={(e) => setName(e.target.value.trim())} style={{ minWidth: "15rem" }} />
      <button className="link small" style={{ marginLeft: ".5rem" }} onClick={() => setName(defaultBatchName())}>use current time</button>
      {!nameOk && <div className="error small">3–60 characters: letters, digits, . - _ ; start with a letter or digit</div>}
    </label>);

  const chip = (b: BatchInfo) => {
    if (b.modified_after_use) return <span className="badge FAIL">changed after use</span>;
    if (b.problems.length && b.files) return <span className="badge FAIL">invalid</span>;
    if (b.status === "used") return <span className="badge done">used · run {b.used_by.map((r) => "#" + r).join(", ")}</span>;
    if (b.status === "unregistered") return <span className="badge warnbadge" title="Found in the bucket but never reserved in the app">outside the app</span>;
    if (b.status === "reserved") return <span className="badge">reserved · waiting for files</span>;
    return <span className="badge ready">ready</span>;
  };

  return (
    <details className="card" open={!ready}>
      <summary><strong>Source files</strong>{" "}
        <span className="muted small">{used.length ? `batch ${used.join(" + ")} · open to add another batch` : "choose how to load the Moodle export"}</span></summary>

      <p className="muted small" style={{ marginTop: ".4rem" }}>Files come in <strong>batches</strong>. A batch is created once with a new name and is never extended: for new or late files make a <strong>new batch</strong> and add it to this run (works already stored are skipped).</p>
      <div className="theme" role="group" aria-label="Source mode" style={{ margin: ".3rem 0 .8rem" }}>
        <button className={mode === "upload" ? "on" : ""} onClick={() => setMode("upload")}>Upload from this computer</button>
        <button className={mode === "cli" ? "on" : ""} onClick={() => setMode("cli")}>Copy with the AWS CLI</button>
        <button className={mode === "existing" ? "on" : ""} onClick={() => setMode("existing")}>Existing batches</button>
      </div>

      {mode === "upload" && (
        <>
          <div className="row" style={{ alignItems: "flex-start" }}>
            {nameField}
            <div><div className="small muted">Moodle “download all submissions” zip{settings.data ? ` (up to ${settings.data.processing.max_upload_mb} MB)` : ""}</div>
              <FilePick label="Choose Moodle zip" accept=".zip" file={zip} onChange={setZip} /></div>
            <div><div className="small muted">Grading worksheet (optional)</div><FilePick label="Choose worksheet CSV" accept=".csv" file={csv} onChange={setCsv} /></div>
          </div>
          {zipTooBig && settings.data && (
            <p className="error">The zip is {mb(zip.size)} — over the {settings.data.processing.max_upload_mb} MB limit.
              Split the export into parts, or copy the files with the AWS CLI (“Copy with the AWS CLI” above).</p>)}
          <p><button className="btn primary" disabled={!zip || !nameOk || zipTooBig || upload.isPending} onClick={() => upload.mutate()}>{upload.isPending ? "Uploading…" : "Upload as a new batch and use it for this run"}</button></p>
          {upload.isError && <p className="error">{String(upload.error).replace(/^ApiError: /, "")}</p>}
        </>)}

      {mode === "cli" && (
        <>
          <div className="row" style={{ alignItems: "flex-end" }}>
            {nameField}
            <button className="btn primary" disabled={!nameOk || reserve.isPending} onClick={() => reserve.mutate()}>Reserve this name</button>
          </div>
          {reserve.isError && <p className="error">{String(reserve.error).replace(/^ApiError: /, "")}</p>}
          {reserved && (
            <div className="card warncard">
              <p><strong>Batch “{reserved.name}” is reserved.</strong> Copy your files to it <strong>once</strong>; do not add files to it later (a batch that changes is refused). Use one of these:</p>
              {[...reserved.commands, reserved.commands_unzipped].map((c) => (
                <div className="row" key={c} style={{ marginBottom: ".3rem" }}><code className="keybox" style={{ flex: 1 }}>{c}</code>
                  <button className="btn sm" onClick={() => copy(c)}>{copied === c ? "Copied ✓" : "Copy"}</button></div>))}
              <p className="small muted">Zip + CSV (first two lines) or the unzipped Moodle folders (last line). When the copy is finished open <em>Existing batches</em> and use it.</p>
              <button className="btn" onClick={() => { setMode("existing"); qc.invalidateQueries({ queryKey: ["batches"] }); }}>I have copied the files — show my batches</button>
            </div>)}
        </>)}

      {mode === "existing" && (
        <>
          <div className="row"><span className="muted small">Every batch found in the bucket, checked for consistency.</span><span className="spacer" /><button className="link small" onClick={() => batches.refetch()}>refresh</button></div>
          {use.isError && <p className="error">{String(use.error).replace(/^ApiError: /, "")}</p>}
          {batches.isLoading && <p className="muted">Loading…</p>}
          <div className="tscroll"><table className="compact">
            <thead><tr><th>Batch</th><th>Contents</th><th>Status</th><th></th></tr></thead>
            <tbody>{batches.data?.batches.map((b) => (
              <tr key={b.name}>
                <td><strong>{b.name}</strong>{b.created_at && <div className="muted small">{new Date(b.created_at).toLocaleString()} · {b.origin}</div>}
                  {b.problems.map((p) => <div key={p} className="error small">{p}</div>)}</td>
                <td className="small">{b.files ? `${b.zips.length} zip · ${b.csvs.length} CSV · ${b.unzipped_submissions} unzipped works · ${mb(b.bytes)}` : "empty"}</td>
                <td>{chip(b)}</td>
                <td className="nowrap">
                  <button className="btn sm primary" disabled={!b.usable || use.isPending || used.includes(b.name)} onClick={() => use.mutate(b.name)}>{used.includes(b.name) ? "in this run" : "Use for this run"}</button>{" "}
                  {b.status !== "used" && <button className="link small" onClick={() => { if (window.confirm(`Delete batch “${b.name}” and its files from the bucket?`)) del.mutate(b.name); }}>delete</button>}
                </td>
              </tr>))}
              {batches.data?.batches.length === 0 && <tr><td colSpan={4} className="muted">No batches yet.</td></tr>}</tbody>
          </table></div>
          {del.isError && <p className="error">{String(del.error).replace(/^ApiError: /, "")}</p>}
        </>)}

      {ready && used.length > 0 && (
        <p style={{ marginBottom: 0 }}><button className="btn sm" onClick={() => reingest.mutate()}>Re-read the run’s batches</button>
          <span className="muted small"> re-copies from the batches already attached (works already stored are skipped)</span></p>)}
    </details>
  );
}
