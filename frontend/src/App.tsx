import { BrowserRouter, Link, Route, Routes } from "react-router-dom";

import { AuthProvider, RequireAuth } from "./auth/AuthContext";
import { ToastProvider } from "./components/ui/Toast";
import { EmptyState } from "./components/ui/primitives";
import AppLayout from "./layouts/AppLayout";
import AuthPage from "./pages/AuthPage";
import DashboardPage from "./pages/DashboardPage";
import DocumentDetailPage from "./pages/DocumentDetailPage";
import DocumentsPage from "./pages/DocumentsPage";
import SearchPage from "./pages/SearchPage";
import SettingsPage from "./pages/SettingsPage";
import WorkspacePage from "./pages/WorkspacePage";

function NotFound() {
  return (
    <EmptyState title="Page not found" action={<Link className="btn btn-secondary" to="/">Go to the dashboard</Link>}>
      This address does not exist.
    </EmptyState>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <ToastProvider>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<AuthPage mode="login" />} />
            <Route path="/register" element={<AuthPage mode="register" />} />
            <Route
              element={
                <RequireAuth>
                  <AppLayout />
                </RequireAuth>
              }
            >
              <Route index element={<DashboardPage />} />
              <Route path="documents" element={<DocumentsPage />} />
              <Route path="documents/:id" element={<DocumentDetailPage />} />
              <Route path="search" element={<SearchPage />} />
              <Route path="workspace" element={<WorkspacePage />} />
              <Route path="workspace/:conversationId" element={<WorkspacePage />} />
              <Route path="settings" element={<SettingsPage />} />
              <Route path="*" element={<NotFound />} />
            </Route>
          </Routes>
        </AuthProvider>
      </ToastProvider>
    </BrowserRouter>
  );
}
