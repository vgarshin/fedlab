// An on/off toggle drawn as a pill with a sliding circle. It is a real checkbox underneath
// (role="switch"), so keyboard, screen readers and forms keep working; only the look is custom.
export default function Switch({ checked, onChange, label, disabled, title }: {
  checked: boolean; onChange: (on: boolean) => void; label?: React.ReactNode; disabled?: boolean; title?: string;
}) {
  return (
    <label className={`switch-label${disabled ? " disabled" : ""}`} title={title}>
      <input type="checkbox" role="switch" className="switch" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      {label && <span>{label}</span>}
    </label>
  );
}
