import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { refreshSession } from "../services/apiClient";
import * as authService from "../services/authService";
import { session } from "../services/session";
import type { User } from "../types/api";
import { LoadingBlock } from "../components/ui/primitives";

type AuthState =
  | { status: "loading" }
  | { status: "anonymous"; notice: string | null }
  | { status: "authenticated"; user: User };

interface AuthApi {
  state: AuthState;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, displayName: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthApi | null>(null);

export function useAuth(): AuthApi {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth needs an AuthProvider");
  return value;
}

export function useUser(): User {
  const { state } = useAuth();
  if (state.status !== "authenticated") throw new Error("useUser is only valid inside RequireAuth");
  return state.user;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>(
    session.hasSession() ? { status: "loading" } : { status: "anonymous", notice: null },
  );

  // Restore the session after a reload: the access token is memory only, so use the refresh token.
  useEffect(() => {
    if (state.status !== "loading") return undefined;
    let cancelled = false;
    (async () => {
      try {
        if (!session.access()) {
          const result = await refreshSession();
          if (result !== "ok") {
            const notice = result === "unavailable" ? "The server could not be reached. Reload the page to try again." : null;
            if (!cancelled) setState({ status: "anonymous", notice });
            return;
          }
        }
        const user = await authService.currentUser();
        if (!cancelled) setState({ status: "authenticated", user });
      } catch {
        if (!cancelled) setState({ status: "anonymous", notice: null });
      }
    })();
    return () => {
      cancelled = true;
    };
    // run once on mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The server refused the session (expired refresh token, revoked session).
  useEffect(
    () =>
      session.onCleared(() => {
        setState((current) =>
          current.status === "authenticated"
            ? { status: "anonymous", notice: "Your session expired. Please sign in again." }
            : current,
        );
      }),
    [],
  );

  const login = useCallback(async (email: string, password: string) => {
    setState({ status: "authenticated", user: await authService.login(email, password) });
  }, []);
  const register = useCallback(async (email: string, password: string, displayName: string) => {
    setState({ status: "authenticated", user: await authService.register(email, password, displayName) });
  }, []);
  const logout = useCallback(async () => {
    await authService.logout();
    setState({ status: "anonymous", notice: null });
  }, []);

  const api = useMemo(() => ({ state, login, register, logout }), [state, login, register, logout]);
  return <AuthContext.Provider value={api}>{children}</AuthContext.Provider>;
}

/** Renders nothing protected until the session is confirmed; sends visitors to the sign-in page. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { state } = useAuth();
  const location = useLocation();
  if (state.status === "loading") {
    return (
      <div className="splash">
        <LoadingBlock label="Restoring your session" />
      </div>
    );
  }
  if (state.status === "anonymous") return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  return <>{children}</>;
}
