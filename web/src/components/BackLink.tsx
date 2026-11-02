import { Link } from "react-router-dom";

// A small "go back" pill with a chevron icon (instead of a text arrow character).
export default function BackLink({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <Link to={to} className="back">
      <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round"><path d="M14.5 5.5 8 12l6.5 6.5" /></svg>
      {children}
    </Link>
  );
}
