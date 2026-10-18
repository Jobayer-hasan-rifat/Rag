import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import { useAuth, useUser } from "../auth/AuthContext";
import { UploadProvider, useUploadDialog } from "../components/UploadDialog";
import { BRAND, BrandMark } from "../components/ui/Brand";
import { Icon, type IconName } from "../components/ui/Icon";
import { Menu, MenuItem, MenuLabel, MenuLink, MenuSeparator } from "../components/ui/Menu";
import { useVisibleViewport } from "../hooks/useViewport";
import { applyTheme, saveTheme, storedTheme, type ThemeChoice } from "../lib/theme";

const NAV: { to: string; label: string; icon: IconName; end?: boolean }[] = [
  { to: "/", label: "Home", icon: "home", end: true },
  { to: "/documents", label: "Documents", icon: "file" },
  { to: "/search", label: "Search", icon: "search" },
  { to: "/workspace", label: "Workspace", icon: "chat" },
];

const TITLES: Record<string, string> = {
  "": "Home",
  documents: "Documents",
  search: "Search",
  workspace: "Workspace",
  settings: "Settings",
};

const THEMES: { value: ThemeChoice; label: string; icon: IconName }[] = [
  { value: "system", label: "Match system", icon: "panel" },
  { value: "light", label: "Light", icon: "sun" },
  { value: "dark", label: "Dark", icon: "moon" },
];

function AccountMenu() {
  const user = useUser();
  const { logout } = useAuth();
  const navigate = useNavigate();
  const [theme, setTheme] = useState<ThemeChoice>(storedTheme);

  return (
    <Menu
      label="Account menu"
      triggerClassName="avatar-btn"
      trigger={
        <span className="avatar" aria-hidden="true">
          {user.display_name.slice(0, 1).toUpperCase()}
        </span>
      }
    >
      <MenuLabel>
        <strong>{user.display_name}</strong>
        <span>{user.email}</span>
      </MenuLabel>
      <MenuSeparator />
      <MenuLink to="/settings" icon="settings">
        Settings
      </MenuLink>
      <MenuSeparator />
      <MenuLabel>Theme</MenuLabel>
      {THEMES.map((option) => (
        <MenuItem
          key={option.value}
          icon={option.icon}
          checked={theme === option.value}
          onSelect={() => {
            setTheme(option.value);
            saveTheme(option.value);
          }}
        >
          {option.label}
        </MenuItem>
      ))}
      <MenuSeparator />
      <MenuItem
        icon="logout"
        onSelect={() => {
          void logout().then(() => navigate("/login", { replace: true }));
        }}
      >
        Sign out
      </MenuItem>
    </Menu>
  );
}

function UploadButton() {
  const upload = useUploadDialog();
  return (
    <button type="button" className="btn btn-primary btn-md topbar-upload" onClick={upload.open}>
      <Icon name="upload" size={16} />
      <span>Add documents</span>
    </button>
  );
}

function Frame() {
  const location = useLocation();
  const main = useRef<HTMLElement>(null);
  const section = location.pathname.split("/")[1] ?? "";
  const inThread = /^\/workspace\/[^/]+/.test(location.pathname);
  const fullBleed = section === "workspace";
  useVisibleViewport();

  useEffect(() => {
    const theme = storedTheme();
    applyTheme(theme);
  }, []);

  // Each section gets its own tab title, and moving to another section moves focus to the content.
  const first = useRef(true);
  useEffect(() => {
    document.title = `${TITLES[section] ?? "Page"} · ${BRAND}`;
    if (first.current) first.current = false;
    else main.current?.focus({ preventScroll: true });
  }, [section]);

  return (
    <div className={`app${inThread ? " app-thread" : ""}`}>
      <a className="skip-link" href="#main" onClick={(event) => { event.preventDefault(); main.current?.focus(); }}>
        Skip to content
      </a>
      <header className="topbar">
        <Link className="brand" to="/" aria-label={`${BRAND}, home`}>
          <BrandMark />
          <span className="brand-name">{BRAND}</span>
        </Link>
        <nav className="mainnav" aria-label="Main">
          {NAV.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end} className={({ isActive }) => `navlink${isActive ? " active" : ""}`}>
              <Icon name={item.icon} size={20} />
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="topbar-actions">
          <UploadButton />
          <AccountMenu />
        </div>
      </header>
      <main id="main" ref={main} className={`main${fullBleed ? " main-full" : ""}`} tabIndex={-1}>
        <Outlet />
      </main>
    </div>
  );
}

export default function AppLayout() {
  return (
    <UploadProvider>
      <Frame />
    </UploadProvider>
  );
}
