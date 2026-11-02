import Brand from "../components/Brand";
import ThemeSwitch from "../components/ThemeSwitch";

export default function LoginPage() {
  const denied = new URLSearchParams(window.location.search).get("error") === "no_access";
  return (
    <main className="login-page">
      <section className="login-hero">
        <div className="brand"><Brand size={32} />Fedlab Grader</div>
        <div><h1>Fair, fast grading of team work</h1>
          <p>AI-assisted checking of case presentations with full transparency: every step, every score and every cost in one place.</p></div>
        <div className="small" style={{ opacity: 0.8 }}>Business solutions laboratory · autumn 2026</div>
        <span className="blob" style={{ width: 420, height: 420, right: -140, top: -120 }} />
        <span className="blob" style={{ width: 260, height: 260, right: 120, bottom: -110 }} />
      </section>
      <section className="login-form">
        <ThemeSwitch />
        <div className="login-card">
          <h2 style={{ marginTop: 0, fontSize: "1.6rem" }}>Sign in</h2>
          <p className="muted">Use your Yandex ID. Access is granted by an administrator.</p>
          {denied && <p className="error">This Yandex account has no access. Ask an admin to add your e-mail.</p>}
          {/* A normal link, not fetch(): the browser must leave our site and go to Yandex's login page. */}
          <a className="btn primary big" href="/api/auth/login">Sign in with Yandex ID</a>
          <p className="small muted" style={{ marginBottom: 0 }}>No access yet? Ask an admin to add your e-mail.</p>
        </div>
      </section>
    </main>
  );
}
