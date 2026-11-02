import { useState } from "react";
import { Link } from "react-router-dom";
import type { Me } from "../types";
import { intro, pages, pagesTitle, roles, tips, workflow, type LL, type L } from "../manualData";

// Two columns: English | Русский, one row per section so the same content lines up side by side.
function Row({ title, en, ru }: { title?: L; en: React.ReactNode; ru: React.ReactNode }) {
  return (
    <section className="man-row">
      <div lang="en">{title && <h2>{title.en}</h2>}{en}</div>
      <div lang="ru">{title && <h2>{title.ru}</h2>}{ru}</div>
    </section>
  );
}
function Go({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <Link to={to} className="go">{children}
      <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round"><path d="M9.5 5.5 16 12l-6.5 6.5" /></svg>
    </Link>);
}
const paras = (a: string[]) => a.map((t, i) => <p key={i}>{t}</p>);
const list = (a: string[]) => <ul>{a.map((t, i) => <li key={i}>{t}</li>)}</ul>;

export default function ManualPage({ me }: { me: Me }) {
  const isAdmin = me.role === "admin";
  const [asViewer, setAsViewer] = useState(false);       // admins can read what viewers see
  const view = isAdmin && !asViewer ? "admin" : "viewer";
  const shown = pages.filter((p) => view === "admin" || !p.adminOnly);
  const pick = (p: (typeof pages)[number]): LL => (view === "viewer" ? p.viewer ?? p.admin : p.admin);

  return (
    <>
      <div className="row">
        <h1 style={{ margin: 0 }}>Manual · Инструкция</h1><span className="spacer" />
        {isAdmin && (
          <span className="theme" role="group" aria-label="Manual version">
            <button className={!asViewer ? "on" : ""} onClick={() => setAsViewer(false)}>Admin manual · для админа</button>
            <button className={asViewer ? "on" : ""} onClick={() => setAsViewer(true)}>Viewer manual · для наблюдателя</button>
          </span>)}
      </div>
      <p className="muted">{view === "admin" ? "You are reading the administrator version." : "You are reading the viewer version."} / {view === "admin" ? "Вы читаете версию для администратора." : "Вы читаете версию для наблюдателя."}</p>

      <div className="man-head"><span>English</span><span>Русский</span></div>

      <Row title={intro.title} en={paras(intro.text.en)} ru={paras(intro.text.ru)} />
      <Row title={roles.title} en={list(roles[view].en)} ru={list(roles[view].ru)} />

      <Row title={pagesTitle} en={<p className="muted small">{view === "admin" ? "Pages in the top menu, left to right." : "Pages available to you in the top menu."}</p>}
           ru={<p className="muted small">{view === "admin" ? "Страницы верхнего меню слева направо." : "Страницы верхнего меню, доступные вам."}</p>} />
      {shown.map((p) => (
        <Row key={p.path}
          en={<div className="card"><h3>{p.name.en} {p.adminOnly && <span className="badge warnbadge">admin</span>} <code className="muted small">{p.path}</code></h3><p>{p.purpose.en}</p>{list(pick(p).en)}
            {p.path !== "/runs/…" && p.path !== "/runs/…/teams/…" && <Go to={p.path}>Open</Go>}</div>}
          ru={<div className="card"><h3>{p.name.ru} {p.adminOnly && <span className="badge warnbadge">admin</span>} <code className="muted small">{p.path}</code></h3><p>{p.purpose.ru}</p>{list(pick(p).ru)}
            {p.path !== "/runs/…" && p.path !== "/runs/…/teams/…" && <Go to={p.path}>Открыть</Go>}</div>} />
      ))}

      <Row title={workflow.title}
        en={<ol>{workflow[view].map((s, i) => <li key={i}>{s.en}</li>)}</ol>} ru={<ol>{workflow[view].map((s, i) => <li key={i}>{s.ru}</li>)}</ol>} />
      <Row title={tips.title} en={list(tips.en)} ru={list(tips.ru)} />
    </>
  );
}
