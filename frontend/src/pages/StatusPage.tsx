import StatusBadge from "../components/StatusBadge";
import { Button } from "../components/ui/primitives";
import { useBackendHealth } from "../hooks/useBackendHealth";

/** Reachability of the API and the services it depends on. */
export default function StatusPage() {
  const health = useBackendHealth();

  return (
    <div className="status-block">
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
      <Button size="sm" icon="retry" onClick={health.refresh}>
        Check again
      </Button>
    </div>
  );
}
