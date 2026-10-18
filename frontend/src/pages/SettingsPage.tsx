import { useNavigate } from "react-router-dom";

import { useAuth, useUser } from "../auth/AuthContext";
import { Button, PageHeader } from "../components/ui/primitives";
import { formatDateTime } from "../lib/format";
import { API_ROOT } from "../services/apiClient";
import StatusPage from "./StatusPage";

export default function SettingsPage() {
  const user = useUser();
  const { logout } = useAuth();
  const navigate = useNavigate();

  return (
    <>
      <PageHeader title="Settings" subtitle="Your account and the connection to the server." />
      <section className="panel" aria-labelledby="account-heading">
        <h2 id="account-heading">Account</h2>
        <dl className="facts">
          <dt>Name</dt>
          <dd>{user.display_name}</dd>
          <dt>Email</dt>
          <dd>{user.email}</dd>
          <dt>Role</dt>
          <dd>{user.role}</dd>
          <dt>Member since</dt>
          <dd>{formatDateTime(user.created_at)}</dd>
        </dl>
        <Button
          icon="logout"
          onClick={async () => {
            await logout();
            navigate("/login", { replace: true });
          }}
        >
          Sign out
        </Button>
        <p className="muted small">
          Your session lasts while this browser tab stays open and is refreshed automatically. Signing out also ends it on the
          server.
        </p>
      </section>
      <section className="panel" aria-labelledby="server-heading">
        <h2 id="server-heading">Server</h2>
        <p className="muted">
          This app talks to <code>{API_ROOT}</code>. Change it with <code>VITE_API_BASE_URL</code> when building or serving the
          frontend.
        </p>
        <StatusPage />
      </section>
    </>
  );
}
