import type { ServiceState } from "../types/health";

const LABELS: Record<ServiceState, string> = {
  checking: "Checking",
  ok: "Healthy",
  unavailable: "Unavailable",
};

interface StatusBadgeProps {
  state: ServiceState;
}

export default function StatusBadge({ state }: StatusBadgeProps) {
  return <span className={`badge badge-${state}`}>{LABELS[state]}</span>;
}
