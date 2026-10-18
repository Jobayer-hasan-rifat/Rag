import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import { useAuth, useUser } from "../auth/AuthContext";
import { Icon, type IconName } from "../components/ui/Icon";
import { IconButton } from "../components/ui/primitives";

const NAV: { to: string; label: string; icon: IconName; end?: boolean }[] = [
  { to: "/", label: "Dashboard", icon: "home", end: true },
  { to: "/documents", label: "Documents", icon: "file" },
  { to: "/search", label: "Search", icon: "search" },
  { to: "/workspace", label: "AI Workspace", icon: "chat" },
  { to: "/settings", label: "Settings", icon: "settings" },
];

const COLLAPSE_KEY = "rag.ui.sidebar.collapsed";

function initialCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === "1";
  } catch {
    return false;
  }
}

export default function AppLayout() {
  const user = useUser();
  const { logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(initialCollapsed);
  const [open, setOpen] = useState(false);

  useEffect(() => setOpen(false), [location.pathname]);
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const toggleCollapsed = () => {
    setCollapsed((value) => {
      try {
        localStorage.setItem(COLLAPSE_KEY, value ? "0" : "1");
      } catch {
        // a preference only
      }
      return !value;
    });
  };

  const signOut = async () => {
    await logout();
    navigate("/login", { replace: true });
  };

  return (
    <div className={`shell${collapsed ? " shell-collapsed" : ""}${open ? " shell-open" : ""}`}>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="sidebar" aria-label="Main">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            <Icon name="layers" size={20} />
          </span>
          <span className="brand-name">Document Workspace</span>
        </div>
        <nav className="nav">
          {NAV.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end} className={({ isActive }) => `nav-link${isActive ? " active" : ""}`} title={item.label}>
              <Icon name={item.icon} />
              <span className="nav-label">{item.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="user-chip" title={user.email}>
            <span className="avatar" aria-hidden="true">
              {user.display_name.slice(0, 1).toUpperCase()}
            </span>
            <span className="user-text">
              <strong>{user.display_name}</strong>
              <small>{user.email}</small>
            </span>
          </div>
          <button type="button" className="nav-link nav-button" onClick={signOut}>
            <Icon name="logout" />
            <span className="nav-label">Sign out</span>
          </button>
          <button type="button" className="nav-link nav-button collapse-toggle" onClick={toggleCollapsed} aria-pressed={collapsed}>
            <Icon name="chevron" />
            <span className="nav-label">{collapsed ? "Expand" : "Collapse"}</span>
          </button>
        </div>
      </aside>
      {open ? <div className="scrim" onClick={() => setOpen(false)} aria-hidden="true" /> : null}
      <div className="content">
        <header className="topbar">
          <IconButton icon="menu" label="Open navigation" onClick={() => setOpen(true)} aria-expanded={open} />
          <span className="topbar-title">Document Workspace</span>
        </header>
        <main id="main" className="main" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
