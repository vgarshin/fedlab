// Light / Auto / Dark. "Auto" follows the operating system; the choice is remembered in this browser.
export type Theme = "light" | "auto" | "dark";
const KEY = "fedlab-theme";

export function getTheme(): Theme {
  try { const t = localStorage.getItem(KEY); if (t === "light" || t === "dark") return t; } catch { /* storage blocked */ }
  return "auto";
}
export function applyTheme(t: Theme) {
  const root = document.documentElement;
  if (t === "auto") delete root.dataset.theme; else root.dataset.theme = t;
  try { if (t === "auto") localStorage.removeItem(KEY); else localStorage.setItem(KEY, t); } catch { /* storage blocked */ }
}
