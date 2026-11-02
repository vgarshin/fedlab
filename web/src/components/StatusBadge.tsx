import type { ItemStatus } from "../types";

// `status` is the prop (input) of this component; the CSS class makes each state a different colour.
export default function StatusBadge({ status, title }: { status: ItemStatus | string; title?: string }) {
  return <span className={`badge ${status}`} title={title}>{status}</span>;
}
