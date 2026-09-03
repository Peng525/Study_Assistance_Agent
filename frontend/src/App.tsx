import { useEffect, useMemo, useState } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { ConfigProvider, theme as antdTheme } from "antd";
import { useAuthStore } from "./store/auth";
import { resolveTheme, useThemeStore } from "./store/theme";
import Login from "./pages/Login";
import Home from "./pages/Home";
import CourseList from "./pages/CourseList";
import Player from "./pages/Player";
import AdminLayout from "./pages/admin/AdminLayout";
import AdminDashboard from "./pages/admin/Dashboard";
import AdminModelConfigs from "./pages/admin/ModelConfigs";
import AdminMaterials from "./pages/admin/Materials";
import AdminUsers from "./pages/admin/Users";
import AdminProjectContext from "./pages/admin/ProjectContext";
import AdminLLMCallLogs from "./pages/admin/LLMCallLogs";
import AdminRedirectIfNeeded from "./components/AdminRedirectIfNeeded";

function RequireAuth({ children }: { children: React.ReactNode }) {
  const token = useAuthStore((s) => s.token);
  const location = useLocation();
  if (!token) return <Navigate to="/login" state={{ from: location }} replace />;
  return <>{children}</>;
}

function RequireAdmin({ children }: { children: React.ReactNode }) {
  const user = useAuthStore((s) => s.user);
  // 角色隔离：user 访问 /admin 重定向到 /
  if (user?.role !== "admin") return <Navigate to="/" replace />;
  return <>{children}</>;
}

export default function App() {
  const mode = useThemeStore((s) => s.mode);
  const resolved = resolveTheme(mode);
  // 系统跟随模式：监听 OS 主题变化实时响应（U9 AC3）
  const [, forceUpdate] = useState(0);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => forceUpdate((n) => n + 1);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", resolved);
  }, [resolved]);

  const antd = useMemo(
    () => ({
      algorithm:
        resolved === "dark" ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
    }),
    [resolved],
  );

  return (
    <ConfigProvider theme={antd}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          path="/"
          element={
            <RequireAuth>
              <AdminRedirectIfNeeded>
                <Home />
              </AdminRedirectIfNeeded>
            </RequireAuth>
          }
        />
        <Route
          path="/courses"
          element={
            <RequireAuth>
              <AdminRedirectIfNeeded>
                <CourseList />
              </AdminRedirectIfNeeded>
            </RequireAuth>
          }
        />
        <Route
          path="/course/:courseId"
          element={
            <RequireAuth>
              <AdminRedirectIfNeeded>
                <Player />
              </AdminRedirectIfNeeded>
            </RequireAuth>
          }
        />
        <Route
          path="/admin"
          element={
            <RequireAuth>
              <RequireAdmin>
                <AdminLayout />
              </RequireAdmin>
            </RequireAuth>
          }
        >
          <Route index element={<AdminDashboard />} />
          <Route path="model-configs" element={<AdminModelConfigs />} />
          <Route path="materials" element={<AdminMaterials />} />
          <Route path="project-context" element={<Navigate to="/admin/columns/courseware" replace />} />
          <Route path="columns/courseware" element={<AdminProjectContext />} />
          <Route path="columns" element={<AdminProjectContext />} />
          <Route path="columns/:sourceId" element={<AdminProjectContext />} />
          <Route path="columns/:sourceId/videos/:courseId" element={<AdminProjectContext />} />
          <Route path="users" element={<AdminUsers />} />
          <Route path="llm-call-logs" element={<AdminLLMCallLogs />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </ConfigProvider>
  );
}
