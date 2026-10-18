import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { useAuth, useUser } from "../auth/AuthContext";
import { Button, PageHeader } from "../components/ui/primitives";
import { formatDateTime } from "../lib/format";
import { saveTheme, storedTheme, type ThemeChoice } from "../lib/theme";
import { API_ROOT } from "../services/apiClient";
import StatusPage from "./StatusPage";

const THEMES: { value: ThemeChoice; label: string; hint: string }[] = [
  { value: "system", label: "Match my device", hint: "Follows the light or dark setting of your device." },
  { value: "light", label: "Light", hint: "Warm white surfaces." },
  { value: "dark", label: "Dark", hint: "Low-glare surfaces for dim rooms." },
];

export default function SettingsPage() {
  const user = useUser();
  const { logout } = useAuth();
  const navigate = useNavigate();
  const [theme, setTheme] = useState<ThemeChoice>(storedTheme);

  return (
    <>
      <PageHeader title="Settings" subtitle="Your account, how the app looks, and the connection to the server." />
      <div className="settings-grid">
        <section className="panel" aria-labelledby="account-heading">
          <h2 id="account-heading">Account</h2>
          <dl className="facts">
            <dt>Name</dt>
            <dd dir="auto">{user.display_name}</dd>
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
            Your session lasts while this browser tab stays open and is refreshed automatically. Signing out also ends it on the server.
          </p>
        </section>

        <section className="panel" aria-labelledby="appearance-heading">
          <h2 id="appearance-heading">Appearance</h2>
          <fieldset className="radio-group">
            <legend className="visually-hidden">Theme</legend>
            {THEMES.map((option) => (
              <label key={option.value} className="radio">
                <input
                  type="radio"
                  name="theme"
                  value={option.value}
                  checked={theme === option.value}
                  onChange={() => {
                    setTheme(option.value);
                    saveTheme(option.value);
                  }}
                />
                <span>
                  <strong>{option.label}</strong>
                  <small className="muted">{option.hint}</small>
                </span>
              </label>
            ))}
          </fieldset>
          <p className="muted small">This choice is stored in this browser only.</p>
        </section>

        <section className="panel" aria-labelledby="server-heading">
          <h2 id="server-heading">Server</h2>
          <p className="muted">
            This app talks to <code>{API_ROOT}</code>. Change it with <code>VITE_API_BASE_URL</code> when building or serving the frontend.
          </p>
          <StatusPage />
        </section>
      </div>
    </>
  );
}
