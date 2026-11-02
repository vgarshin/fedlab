// The app icon: a sheet of paper with three ticked boxes (same artwork as public/favicon.svg).
export default function Brand({ size = 28 }: { size?: number }) {
  return (
    <svg viewBox="0 0 64 64" width={size} height={size} aria-hidden="true">
      <rect width="64" height="64" rx="14" fill="var(--brand)" /><rect x="13" y="8" width="38" height="48" rx="5" fill="#fff" />
      <g fill="none" stroke="#0a2896" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
        <rect x="18.5" y="15" width="9" height="9" rx="2.2" /><path d="M20.6 19.6l2.2 2.2 3.4-4.2" />
        <rect x="18.5" y="28" width="9" height="9" rx="2.2" /><path d="M20.6 32.6l2.2 2.2 3.4-4.2" />
        <rect x="18.5" y="41" width="9" height="9" rx="2.2" /><path d="M20.6 45.6l2.2 2.2 3.4-4.2" />
      </g>
      <g stroke="#a9c2e8" strokeWidth="3" strokeLinecap="round"><path d="M32 19.5h13M32 32.5h13M32 45.5h13" /></g>
    </svg>
  );
}
