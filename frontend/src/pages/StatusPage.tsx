import StatusBadge from "../components/StatusBadge";
import { useBackendHealth } from "../hooks/useBackendHealth";

export default function StatusPage() {
  const health = useBackendHealth();

  return (
    <section>
      <h2>Backend status</h2>
      <ul className="status-list">
        <li>
          <span>API</span>
          <StatusBadge state={health.api} />
        </li>
        <li>
          <span>Database</span>
          <StatusBadge state={health.database} />
        </li>
        <li>
          <span>Redis</span>
          <StatusBadge state={health.redis} />
        </li>
      </ul>
      <button type="button" onClick={health.refresh}>
        Refresh
      </button>
    </section>
  );
}
