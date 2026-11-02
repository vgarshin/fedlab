"""Generates the three static proposal pages (login, run, analytics) + index. Static demo data; no backend needed."""
ICON = '''<svg viewBox="0 0 64 64" aria-hidden="true"><rect width="64" height="64" rx="14" fill="var(--brand)"/><rect x="13" y="8" width="38" height="48" rx="5" fill="#fff"/>
<g fill="none" stroke="#0a2896" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><rect x="18.5" y="15" width="9" height="9" rx="2.2"/><path d="M20.6 19.6l2.2 2.2 3.4-4.2"/>
<rect x="18.5" y="28" width="9" height="9" rx="2.2"/><path d="M20.6 32.6l2.2 2.2 3.4-4.2"/><rect x="18.5" y="41" width="9" height="9" rx="2.2"/><path d="M20.6 45.6l2.2 2.2 3.4-4.2"/></g>
<g stroke="#a9c2e8" stroke-width="3" stroke-linecap="round"><path d="M32 19.5h13M32 32.5h13M32 45.5h13"/></g></svg>'''

HEAD = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Fedlab Grader · {title}</title><link rel="stylesheet" href="vtb.css"></head><body>'
THEME_JS = '''<script>
(function(){var r=document.documentElement,k="fedlab-proposal-theme";try{var t=localStorage.getItem(k);if(t)r.dataset.theme=t;}catch(e){}
function set(t){if(t==="auto"){delete r.dataset.theme;try{localStorage.removeItem(k)}catch(e){}}else{r.dataset.theme=t;try{localStorage.setItem(k,t)}catch(e){}}mark();}
function mark(){var cur=r.dataset.theme||"auto";document.querySelectorAll(".theme button").forEach(function(b){b.classList.toggle("on",b.dataset.t===cur)})}
document.querySelectorAll(".theme button").forEach(function(b){b.onclick=function(){set(b.dataset.t)}});mark();})();
</script></body></html>'''
THEME = '<span class="theme"><button data-t="light">Light</button><button data-t="auto">Auto</button><button data-t="dark">Dark</button></span>'

def topbar(active):
    items = [("Runs", "run.html"), ("Analytics", "analytics.html"), ("Playground", "#"), ("Prompts", "#"), ("Models", "#"), ("Settings", "#"), ("Users", "#"), ("API", "#")]
    nav = "".join(f'<a href="{h}"{" class=active" if n == active else ""}>{n}</a>' for n, h in items)
    return f'<header class="topbar"><a class="brand" href="run.html">{ICON}Fedlab Grader</a><nav class="nav">{nav}</nav><span class="spacer"></span><span class="who">Dev User · admin</span>{THEME}<a href="login.html" class="small">Log out</a></header>'

# ---------------- login
login = HEAD.format(title="Sign in") + f'''<main class="login"><section class="hero">
<div class="brand">{ICON}Fedlab Grader</div>
<div><h1>Fair, fast grading of team work</h1><p>AI-assisted checking of case presentations with full transparency: every step, every score and every cost in one place.</p></div>
<div class="small" style="opacity:.8">Business solutions laboratory · autumn 2026</div>
<span class="blob" style="width:420px;height:420px;right:-140px;top:-120px"></span><span class="blob" style="width:260px;height:260px;right:120px;bottom:-110px"></span></section>
<section class="formside">{THEME}<div class="formcard"><h2 style="margin-top:0;font-size:1.6rem">Sign in</h2><p class="muted">Use your Yandex ID. Access is granted by an administrator.</p>
<a class="btn primary big" href="run.html">Sign in with Yandex ID</a>
<p class="small muted" style="margin-bottom:0">No access yet? Ask an admin to add your e-mail.</p></div></section></main>''' + THEME_JS

# ---------------- run
works = [("Команда 1", "Тестовое решение 1.pdf", 16, "8941", False, 42, "75"), ("Команда 2", "Тестовое решение 2.pdf", 9, "3645", False, 62, "43"),
         ("Команда 3", "Тестовое решение 3 — плохое, другая тема.pdf", 6, "1344", False, 55, "0 (DQ)"), ("Команда 4", "Тестовое решение 4.pdf", 5, "10181", True, 55, "60"),
         ("Команда 5", "Тестовое решение 5.pdf", 4, "9016", False, 55, "68")]
rows = ""
for n, f, s, c, over, ai, g in works:
    over_chip = ' <span class="chip warn">⚠ over limit</span>' if over else ""
    rows += f'<tr><td><a href="#">{n}</a></td><td class="muted">{f}</td><td class="num">{s}</td><td class="num">{c}{over_chip}</td><td><span class="chip ok">PASS</span></td><td>✓</td><td><span class="chip ok">done</span></td><td class="num">{ai}%</td><td class="grade num">{g}</td></tr>'
def stage(name, ocr=False, last=True):
    sw = '<span class="switch"><i></i>OCR</span>' if ocr else "<span></span>"
    return f'<div class="stage"><div class="top"><span>{name}</span><span class="muted small">5/5</span></div><div class="bar"><i style="width:100%"></i></div><div class="foot"><span class="muted small">complete</span>{sw}{"<button class=\'btn sm\'>Resume / run</button>" if last else "<span></span>"}</div></div>'
run = HEAD.format(title="Run") + topbar("Runs") + f'''<main class="page"><div class="run-head"><div><a class="small" href="#">← Runs</a><h1>Autumn lab · demo <span class="chip ok" style="vertical-align:middle">ready</span></h1>
<div class="muted small">5 works · grading grade_short v1 × DeepSeek V4.1 Flash · AI detection ai_detect v1 · spent 17.25 of 1000 ₽</div></div>
<div class="actions"><button class="btn primary">▶ Resume / run all stages</button><button class="btn">Rebuild export</button><button class="btn">⬇ Results (zip)</button></div></div>
<details class="card"><summary>Source files <span class="muted small">(loaded — open to add late files or re-read the source)</span></summary></details>
<details class="card"><summary>Configuration &amp; cost <span class="muted small">spent 17.25 of 1000 ₽</span></summary></details>
<div class="strip">{stage("1 Upload", last=False)}{stage("2 Extract", ocr=True)}{stage("3 AI detect")}{stage("4 Grade")}</div>
<h2>Works (5)</h2><table><thead><tr><th>Team</th><th>File</th><th>Slides</th><th>Chars</th><th>Pre-check</th><th>Worksheet</th><th>Extract</th><th>AI prob.</th><th>Grade</th></tr></thead><tbody>{rows}</tbody></table>
<details class="card"><summary>Export <span class="muted small">10/23/2026, 6:40 PM · 5 works · 9 run-level files</span></summary></details></main>''' + THEME_JS

# ---------------- analytics
def bars(vals, labels, title, sub, limit_idx=None, W=620, H=240):
    L, R, T, B = 34, 8, 20, 34
    top = max(vals) if max(vals) > 2 else 2
    slot = (W - L - R) / len(vals); bw = min(26, slot - 4)
    y = lambda v: T + (H - T - B) * (1 - v / top)
    g = "".join(f'<line class="gl" x1="{L}" x2="{W-R}" y1="{y(t)}" y2="{y(t)}"/><text class="ax" x="{L-6}" y="{y(t)+4}" text-anchor="end">{t}</text>' for t in range(0, top + 1))
    b = ""
    for i, (v, lab) in enumerate(zip(vals, labels)):
        x = L + slot * i + (slot - bw) / 2; h = (H - B) - y(v)
        if v:
            b += f'<path d="M{x},{H-B} V{y(v)+6} Q{x},{y(v)} {x+6},{y(v)} H{x+bw-6} Q{x+bw},{y(v)} {x+bw},{y(v)+6} V{H-B} Z" fill="url(#g)"/>'
            if v == max(vals): b += f'<text class="lbl" x="{x+bw/2}" y="{y(v)-6}" text-anchor="middle">{v}</text>'
        b += f'<text class="ax" x="{L+slot*i+slot/2}" y="{H-B+16}" text-anchor="middle">{lab}</text>'
    lim = ""
    if limit_idx is not None:
        lx = L + slot * limit_idx
        lim = f'<line x1="{lx}" x2="{lx}" y1="{T}" y2="{H-B}" stroke="var(--warn)" stroke-width="1.5" stroke-dasharray="4 3"/><text class="ax" x="{lx+5}" y="{T+10}">limit 10 000</text>'
    return (f'<div class="card"><div class="row"><h3 style="margin:0">{title}</h3><span class="spacer"></span><a class="small" href="#">Show as table</a></div><div class="muted small">{sub}</div>'
            f'<svg viewBox="0 0 {W} {H}" width="100%"><defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--bar-2)"/><stop offset="1" stop-color="var(--bar)"/></linearGradient></defs>{g}{b}'
            f'<line x1="{L}" x2="{W-R}" y1="{H-B}" y2="{H-B}" stroke="var(--muted)" opacity=".5"/>{lim}</svg></div>')
grade = bars([0, 0, 0, 0, 1, 0, 2, 1, 0, 0], [f"{a}–{a+9}" for a in range(0, 100, 10)], "Distribution of grades", "Graded works only, grade out of 100 in steps of 10")
length = bars([1, 1, 0, 0, 2, 1], ["0–2k", "2–4k", "4–6k", "6–8k", "8–10k", "10–12k"], "Distribution of lengths", "Characters with spaces, steps of 2 000 · 1 work over the 10000 limit", limit_idx=5)
def top(title, rowsx):
    body = "".join(f'<tr><td>{i}</td><td><a href="#">{n}</a></td><td class="grade num">{g}</td><td class="num">{c}</td><td class="num">{a}%</td></tr>' for i, (n, g, c, a) in enumerate(rowsx, 1))
    return f'<div class="card"><h3>{title}</h3><table style="box-shadow:none"><thead><tr><th>#</th><th>Work</th><th>Grade</th><th>Length, chars</th><th>AI prob.</th></tr></thead><tbody>{body}</tbody></table></div>'
tiles = [("Teams (works)", "5", ""), ("Members", "10", ""), ("Graded", "4", ""), ("Disqualified", "1", ""), ("Not graded yet", "0", ""), ("Average grade", "61.5", "hl"), ("Median grade", "68", ""), ("Money spent", "17.25 ₽", "hl"), ("Per work", "3.45 ₽", "")]
tiles_html = "".join(f'<div class="tile {k}"><div class="l">{l}</div><div class="v">{v}</div></div>' for l, v, k in tiles)
analytics = HEAD.format(title="Analytics") + topbar("Analytics") + f'''<main class="page"><div class="row"><h1 style="margin:0">Analytics</h1><span class="spacer"></span><label class="small muted">Run <select style="padding:.4rem .7rem;border-radius:10px;border:1px solid var(--line);background:var(--surface);color:var(--text);font:inherit"><option>#9000 Autumn lab · demo (latest)</option></select></label></div>
<div class="tiles" style="margin-top:1rem">{tiles_html}</div><div class="charts" style="margin-top:.8rem">{grade}{length}</div>
<div class="charts">{top("Top 5 best works", [("Команда 1", 75, "8941", 42), ("Команда 5", 68, "9016", 55), ("Команда 4", 60, "10181", 55), ("Команда 2", 43, "3645", 62)])}{top("5 lowest graded works", [("Команда 2", 43, "3645", 62), ("Команда 4", 60, "10181", 55), ("Команда 5", 68, "9016", 55), ("Команда 1", 75, "8941", 42)])}</div></main>''' + THEME_JS

index = HEAD.format(title="Proposal") + '<main class="page"><h1>Style proposal · corporate blue</h1><p class="muted">Open the three pages; use Light / Auto / Dark at the top right. Static demo data, nothing here is connected to the app.</p><div class="row"><a class="btn primary" href="login.html">Login</a><a class="btn primary" href="run.html">Run</a><a class="btn primary" href="analytics.html">Analytics</a></div></main>' + THEME_JS
for name, html in (("login", login), ("run", run), ("analytics", analytics), ("index", index)):
    open(f"{name}.html", "w", encoding="utf-8").write(html)
print("written")
