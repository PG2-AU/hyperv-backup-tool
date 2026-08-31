import { Navigate, Route, Routes } from "react-router-dom";

import { AppShellLayout } from "@/layout/AppShellLayout";
import { LoginPage } from "@/pages/LoginPage";
import { RequireAuth } from "@/layout/RequireAuth";
import { DashboardPage } from "@/pages/DashboardPage";
import { VmsPage } from "@/pages/VmsPage";
import { StoragePage } from "@/pages/StoragePage";
import { JobsPage } from "@/pages/JobsPage";
import { RestorePage } from "@/pages/RestorePage";
import { SettingsPage } from "@/pages/SettingsPage";
import { DocsPage } from "@/pages/DocsPage";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <AppShellLayout />
          </RequireAuth>
        }
      >
        <Route path="/" element={<DashboardPage />} />
        <Route path="/vms" element={<VmsPage />} />
        <Route path="/storage" element={<StoragePage />} />
        <Route path="/jobs" element={<JobsPage />} />
        <Route path="/restore" element={<RestorePage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/docs" element={<DocsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
