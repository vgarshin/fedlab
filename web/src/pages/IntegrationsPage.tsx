import { useState } from "react";
import Switch from "../components/Switch";

// Design demo (no backend yet): a future "Integrations" page for connections to external
// systems — Moodle Web Services, object storage and LLM providers. Everything below is
// sample data rendered with the normal design system, so it can be reviewed as a mockup.
type LinkState = "connected" | "not_connected" | "checking";

export default function IntegrationsPage() {
  const [moodle, setMoodle] = useState({ url: "https://moodle.university.ru", token: "yus2mvkq3fj1a0", service: "fedlab_grading" });
  const [moodleState, setMoodleState] = useState<LinkState>("not_connected");
  const [enabled, setEnabled] = useState(false);
  const testMoodle = () => {
    setMoodleState("checking");
    setTimeout(() => setMoodleState("connected"), 900); // demo only: fake a successful check
  };
  const Badge = ({ s }: { s: LinkState }) => (
    <span className={`badge ${s === "connected" ? "done" : s === "checking" ? "running" : "failed"}`}>
      {s === "connected" ? "connected" : s === "checking" ? "checking…" : "not connected"}
    </span>);
  return (
    <>
      <h1>Integrations <span className="badge warnbadge">design demo · sample data</span></h1>
      <p className="muted small">Connections to external systems. Nothing on this page is wired to the backend yet — it is a mockup for review.</p>

      <div className="tiles">
        <div className="tile"><div className="tile-label">Moodle Web Services</div><div className="tile-value" style={{ fontSize: "1.1rem" }}><Badge s={moodleState} /></div></div>
        <div className="tile"><div className="tile-label">Object storage (T1 S3)</div><div className="tile-value" style={{ fontSize: "1.1rem" }}><span className="badge done">connected</span></div></div>
        <div className="tile"><div className="tile-label">LLM providers</div><div className="tile-value">2 / 2</div></div>
        <div className="tile"><div className="tile-label">Last end-to-end check</div><div className="tile-value">13:40</div></div>
      </div>

      <div className="settings-grid">
        <div className="card wide">
          <h3>Moodle · Web Services <Badge s={moodleState} /></h3>
          <p className="muted small">Planned v2 path: pull submissions and push grades over Moodle Web Services (the file-based zip + worksheet stays the v1 path). Needs a service account with <code>mod_assign</code> capabilities and a token created by the Moodle admin.</p>
          <div className="model-row"><strong>Site URL</strong>
            <input value={moodle.url} onChange={(e) => setMoodle({ ...moodle, url: e.target.value })} /></div>
          <div className="model-row"><strong>Webservice token</strong>
            <input type="password" value={moodle.token} onChange={(e) => setMoodle({ ...moodle, token: e.target.value })} />
            <span className="muted small">stored in secrets, never shown again</span></div>
          <div className="model-row"><strong>Service name</strong>
            <select value={moodle.service} onChange={(e) => setMoodle({ ...moodle, service: e.target.value })}>
              <option value="fedlab_grading">fedlab_grading</option>
            </select></div>
          <p className="row">
            <button className="btn" onClick={testMoodle} disabled={moodleState === "checking"}>Test connection</button>
            <button className="btn primary">Save</button>
          </p>
          <table className="compact" style={{ marginTop: ".6rem" }}>
            <thead><tr><th>Capability check</th><th>Result</th></tr></thead>
            <tbody>
              <tr><td><code>mod_assign_get_submissions</code> — download decks</td><td><span className="badge done">allowed</span></td></tr>
              <tr><td><code>mod_assign_save_grade</code> — push grades and feedback</td><td><span className="badge done">allowed</span></td></tr>
              <tr><td>REST protocol enabled for the service</td><td><span className="badge warnbadge">not checked</span></td></tr>
            </tbody>
          </table>
        </div>

        <div className="card">
          <h3>Object storage · T1 Cloud S3 <span className="badge done">connected</span></h3>
          <div className="model-row"><strong>Endpoint</strong><span><code>https://api.s3.dp.t1.cloud</code></span></div>
          <div className="model-row"><strong>Buckets</strong><span><code>fedlab-grade-data</code> (input, work, cache) · <code>fedlab-result-data</code> (exports)</span></div>
          <div className="model-row"><strong>Last check</strong><span>today 13:40 · both buckets writable</span></div>
          <p className="row"><button className="btn">Check access now</button></p>
          <p className="muted small">The input prefix is written once and never overwritten; the LLM/OCR caches live under <code>_cache/</code>. Access is verified by running code, keys are never displayed.</p>
        </div>

        <div className="card">
          <h3>LLM providers <span className="badge done">2 configured</span></h3>
          <p><strong>Yandex AI Studio</strong> <span className="badge done">connected</span> <span className="muted small">folder b1gerja4eh37e1jfs0fp · default</span></p>
          <p><strong>Cloud.ru Foundation Models</strong> <span className="badge running">key saved</span> <span className="muted small">disabled by default</span></p>
          <div style={{ marginTop: ".4rem" }}>
            <Switch checked={enabled} onChange={setEnabled} label={<><strong>Allow Cloud.ru</strong> <span className="muted small">(models become selectable on the Models page)</span></>} />
          </div>
          <p className="row" style={{ marginTop: ".5rem" }}><button className="btn">Check both providers</button></p>
          <p className="muted small">Keys live in <code>secrets.json</code> via compose secrets; prices come from the model registry, never from code.</p>
        </div>
      </div>
    </>
  );
}
