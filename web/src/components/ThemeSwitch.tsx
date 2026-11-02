import { useState } from "react";
import { applyTheme, getTheme, type Theme } from "../theme";

export default function ThemeSwitch() {
  const [t, setT] = useState<Theme>(getTheme());
  const pick = (v: Theme) => { applyTheme(v); setT(v); };
  return (
    <span className="theme" role="group" aria-label="Colour theme">
      {(["light", "auto", "dark"] as Theme[]).map((v) => <button key={v} className={t === v ? "on" : ""} onClick={() => pick(v)}>{v[0].toUpperCase() + v.slice(1)}</button>)}
    </span>
  );
}
