import { useId, useState } from "react";
import type { Bin } from "../types";

// A dependency-free SVG column chart for one series (a histogram). Specs follow the data-viz rules:
// thin columns (<= 24px) with a rounded data end and a square baseline, hairline recessive grid,
// value labels only where they matter (the tallest bin), hover tooltip on a column wider than the mark,
// and a "table" view for accessibility. One series => no legend; the title names it.
interface Props {
  title: string; subtitle?: string; bins: Bin[]; xLabel: (b: Bin) => string; tipTitle: (b: Bin) => string;
  unit: string;                       // what is counted, e.g. "works"
  limitAt?: { index: number; label: string };   // optional vertical reference line before bin `index`
}

export default function BarChart({ title, subtitle, bins, xLabel, tipTitle, unit, limitAt }: Props) {
  const [tip, setTip] = useState<{ i: number; x: number } | null>(null);
  const [table, setTable] = useState(false);
  const gid = "bg" + useId().replace(/:/g, "");   // unique gradient id per chart
  const W = 640, H = 230, L = 34, R = 8, T = 16, B = 34;
  const max = Math.max(1, ...bins.map((b) => b.count));
  const top = max <= 4 ? max : Math.ceil(max / 5) * 5;                 // clean axis maximum
  const ticks = top <= 4 ? Array.from({ length: top + 1 }, (_, i) => i) : [0, top / 5 * 1, top / 5 * 2, top / 5 * 3, top / 5 * 4, top].filter((v, i, a) => a.indexOf(v) === i);
  const slot = (W - L - R) / Math.max(1, bins.length);
  const bw = Math.min(24, slot - 2);
  const y = (v: number) => T + (H - T - B) * (1 - v / top);
  const maxIdx = bins.findIndex((b) => b.count === max);
  const barPath = (x: number, h: number) => {          // 4px rounded top corners, square at the baseline
    const r = Math.min(4, bw / 2, h);
    const y0 = H - B;
    return `M${x},${y0} V${y0 - h + r} Q${x},${y0 - h} ${x + r},${y0 - h} H${x + bw - r} Q${x + bw},${y0 - h} ${x + bw},${y0 - h + r} V${y0} Z`;
  };
  return (
    <div className="card chart">
      <div className="row"><h3 style={{ margin: 0 }}>{title}</h3><span className="spacer" />
        <button className="link small" onClick={() => setTable(!table)}>{table ? "Show chart" : "Show as table"}</button></div>
      {subtitle && <p className="muted small" style={{ margin: ".2rem 0" }}>{subtitle}</p>}
      {bins.length === 0 ? <p className="muted">No data yet.</p> : table ? (
        <div className="tscroll"><table><thead><tr><th>Range</th><th>Count</th><th>Works</th></tr></thead>
          <tbody>{bins.map((b, i) => <tr key={i}><td>{xLabel(b)}</td><td>{b.count}</td><td className="small muted">{b.teams.join(", ")}{b.count > b.teams.length && "…"}</td></tr>)}</tbody></table></div>
      ) : (
        <div style={{ position: "relative" }}>
          <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${title}: ${bins.map((b) => `${xLabel(b)} ${b.count}`).join(", ")}`} style={{ width: "100%", height: "auto" }}>
            <defs><linearGradient id={gid} x1="0" y1="0" x2="0" y2="1"><stop offset="0" className="stop-top" /><stop offset="1" className="stop-bot" /></linearGradient></defs>
            {ticks.map((t) => (
              <g key={t}><line x1={L} x2={W - R} y1={y(t)} y2={y(t)} className="grid" />
                <text x={L - 6} y={y(t) + 4} textAnchor="end" className="axis">{t}</text></g>))}
            {bins.map((b, i) => {
              const cx = L + slot * i + (slot - bw) / 2;
              const h = H - B - y(b.count);
              return (
                <g key={i}>
                  {b.count > 0 && <path d={barPath(cx, Math.max(h, 2))} fill={`url(#${gid})`} opacity={tip && tip.i !== i ? 0.55 : 1} />}
                  {i === maxIdx && b.count > 0 && <text x={cx + bw / 2} y={y(b.count) - 5} textAnchor="middle" className="val">{b.count}</text>}
                  <text x={L + slot * i + slot / 2} y={H - B + 16} textAnchor="middle" className="axis">{xLabel(b)}</text>
                  {/* hit target: the whole column slot, larger than the mark */}
                  <rect x={L + slot * i} y={T} width={slot} height={H - T - B} fill="transparent"
                    onMouseEnter={() => setTip({ i, x: L + slot * i + slot / 2 })} onMouseLeave={() => setTip(null)} />
                </g>);
            })}
            <line x1={L} x2={W - R} y1={H - B} y2={H - B} className="axisline" />
            {limitAt && (
              <g><line x1={L + slot * limitAt.index} x2={L + slot * limitAt.index} y1={T} y2={H - B} className="limit" />
                <text x={L + slot * limitAt.index + 4} y={T + 10} className="axis">{limitAt.label}</text></g>)}
          </svg>
          {tip && (
            <div className="tip" style={{ left: `${(tip.x / W) * 100}%` }}>
              <strong>{tipTitle(bins[tip.i])}</strong><br />{bins[tip.i].count} {unit}
              {bins[tip.i].teams.length > 0 && <div className="small muted">{bins[tip.i].teams.join(", ")}{bins[tip.i].count > bins[tip.i].teams.length && "…"}</div>}
            </div>)}
        </div>
      )}
    </div>
  );
}
