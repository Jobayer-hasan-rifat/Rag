import { useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import { BRAND, BrandMark } from "../components/ui/Brand";
import { Button, ErrorNotice, Field, Notice, PasswordInput, TextInput } from "../components/ui/primitives";

/** The password rules the server enforces, checked here only to explain them early. */
export function passwordProblem(password: string): string | null {
  if (password.length < 8) return "Use at least 8 characters.";
  if (!/[a-z]/.test(password)) return "Include a lowercase letter.";
  if (!/[A-Z]/.test(password)) return "Include an uppercase letter.";
  if (!/\d/.test(password)) return "Include a digit.";
  return null;
}

export default function AuthPage({ mode }: { mode: "login" | "register" }) {
  const { state, login, register } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? "/";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [touched, setTouched] = useState(false);

  if (state.status === "authenticated") return <Navigate to={from} replace />;

  const registering = mode === "register";
  const passwordError = registering && touched ? passwordProblem(password) : null;
  const nameError = registering && touched && !name.trim() ? "Enter the name to show in the app." : null;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setTouched(true);
    if (registering && (passwordProblem(password) || !name.trim())) return;
    setBusy(true);
    setError(null);
    try {
      if (registering) await register(email.trim(), password, name.trim());
      else await login(email.trim(), password);
      navigate(from, { replace: true });
    } catch (failure) {
      setError(failure);
      setBusy(false);
    }
  };

  return (
    <div className="auth-page">
      <div className="auth-card">
        <div className="auth-brand">
          <BrandMark size={32} />
          <span>{BRAND}</span>
        </div>
        <h1>{registering ? "Create your account" : "Sign in"}</h1>
        <p className="muted auth-lead">
          {registering
            ? "Your documents and conversations are private to your account."
            : "Search and ask questions across your own documents."}
        </p>
        {state.status === "anonymous" && state.notice ? <Notice tone="warn">{state.notice}</Notice> : null}
        <form onSubmit={submit} noValidate>
          {registering ? (
            <Field label="Display name" error={nameError}>
              {(props) => <TextInput {...props} value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" required autoFocus />}
            </Field>
          ) : null}
          <Field label="Email">
            {(props) => <TextInput {...props} type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" inputMode="email" autoCapitalize="none" spellCheck={false} required autoFocus={!registering} />}
          </Field>
          <Field label="Password" hint={registering ? "At least 8 characters with a lowercase letter, an uppercase letter and a digit." : undefined} error={passwordError}>
            {(props) => (
              <PasswordInput
                {...props}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={registering ? "new-password" : "current-password"}
                required
              />
            )}
          </Field>
          {error ? <ErrorNotice error={error} compact /> : null}
          <Button type="submit" variant="primary" busy={busy} disabled={!email || !password}>
            {registering ? "Create account" : "Sign in"}
          </Button>
        </form>
        <p className="auth-switch">
          {registering ? (
            <>
              Already have an account? <Link to="/login">Sign in</Link>
            </>
          ) : (
            <>
              New here? <Link to="/register">Create an account</Link>
            </>
          )}
        </p>
      </div>
    </div>
  );
}
