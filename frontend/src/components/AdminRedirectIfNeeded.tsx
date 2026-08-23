import { Navigate } from "react-router-dom";
import { useAuthStore } from "../store/auth";

// 路由守卫：admin 任何时候访问学习端路径都重定向到 /admin
// - 与 RequireAdmin 条件互补（RequireAdmin 拦截 user 进 /admin；本守卫拦截 admin 进学习端），无循环
// - 置于 RequireAuth 内层：先验 token 未登录跳 /login，再按角色拦截
export default function AdminRedirectIfNeeded({ children }: { children: React.ReactNode }) {
  const user = useAuthStore((s) => s.user);
  if (user?.role === "admin") return <Navigate to="/admin" replace />;
  return <>{children}</>;
}