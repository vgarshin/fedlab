import { useId } from "react";

// A styled file button: an upload icon (arrow pointing down onto a line) instead of the browser's plain "Choose File".
// The real <input type="file"> stays in the page (visually hidden), so keyboard and screen readers keep working.
export default function FilePick({ label, accept, file, onChange }: { label: string; accept: string; file: File | null; onChange: (f: File | null) => void }) {
  const id = useId();
  return (
    <span className="filepick">
      <input id={id} className="sr-only" type="file" accept={accept} onChange={(e) => onChange(e.target.files?.[0] ?? null)} />
      <label htmlFor={id} className="btn">
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 3.5v10.5M7.5 10 12 14.5 16.5 10M4.5 20h15" />
        </svg>
        {label}
      </label>
      <span className={`small ${file ? "" : "muted"}`}>{file ? file.name : "no file chosen"}</span>
    </span>
  );
}
