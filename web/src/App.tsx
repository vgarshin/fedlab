import { Navigate, Route, Routes } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "./api";
import type { Me } from "./types";
import Layout from "./components/Layout";
import LoginPage from "./pages/LoginPage";
import RunsPage from "./pages/RunsPage";
import RunPage from "./pages/RunPage";
import TeamPage from "./pages/TeamPage";
import UsersPage from "./pages/UsersPage";
import PromptsPage from "./pages/PromptsPage";
import ModelsPage from "./pages/ModelsPage";
import SettingsPage from "./pages/SettingsPage";
import AnalyticsPage from "./pages/AnalyticsPage";
import PlaygroundPage from "./pages/PlaygroundPage";
import ApiPage from "./pages/ApiPage";
import ManualPage from "./pages/ManualPage";
import IntegrationsPage from "./pages/IntegrationsPage";

// useQuery(key, fn): "fetch this data, cache it under this key, tell me loading/error/data".
// Here it answers the question "who am I?"; a 401 means "not logged in" and shows the login page.
export function useMe() {
  return useQuery<Me | null>({
    queryKey: ["me"],
    queryFn: async () => {
      try { return await api.get<Me>("/api/auth/me"); }
      catch (e) { if (e instanceof ApiError && e.status === 401) return null; throw e; }
    },
  });
}

export default function App() {
  const me = useMe();
  if (me.isLoading) return <p className="center muted">Loading…</p>;
  if (me.isError) return <p className="center error">Cannot reach the server: {String(me.error)}</p>;
  if (!me.data) return <LoginPage />;

  // <Routes> shows exactly one page depending on the URL. ":runId" is a URL parameter that the
  // page reads with useParams().
  return (
    <Layout me={me.data}>
      <Routes>
        <Route path="/" element={<RunsPage me={me.data} />} />
        <Route path="/runs/:runId" element={<RunPage me={me.data} />} />
        <Route path="/runs/:runId/teams/:teamId" element={<TeamPage />} />
        <Route path="/analytics" element={<AnalyticsPage />} />
        <Route path="/prompts" element={me.data.role === "admin" ? <PromptsPage me={me.data} /> : <Navigate to="/" />} />
        <Route path="/models" element={me.data.role === "admin" ? <ModelsPage me={me.data} /> : <Navigate to="/" />} />
        <Route path="/playground" element={<PlaygroundPage />} />
        <Route path="/settings" element={me.data.role === "admin" ? <SettingsPage /> : <Navigate to="/" />} />
        <Route path="/integrations" element={me.data.role === "admin" ? <IntegrationsPage /> : <Navigate to="/" />} />
        <Route path="/developers" element={me.data.role === "admin" ? <ApiPage /> : <Navigate to="/" />} />
        <Route path="/manual" element={<ManualPage me={me.data} />} />
        <Route path="/users" element={me.data.role === "admin" ? <UsersPage /> : <Navigate to="/" />} />
        <Route path="*" element={<Navigate to="/" />} />
      </Routes>
    </Layout>
  );
}
