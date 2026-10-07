import type { ReactNode } from "react";

interface AppLayoutProps {
  children: ReactNode;
}

export default function AppLayout({ children }: AppLayoutProps) {
  return (
    <div className="app-shell">
      <header className="app-header">
        <h1>RAG Platform</h1>
      </header>
      <main className="app-main">{children}</main>
    </div>
  );
}
