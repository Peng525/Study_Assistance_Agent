import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import AdminLayout from "../pages/admin/AdminLayout";
import UserMenu from "../components/UserMenu";
import { useAuthStore } from "../store/auth";
import { useThemeStore } from "../store/theme";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: { post: vi.fn() },
}));

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  useAuthStore.getState().login("token", { user_id: 1, username: "admin", role: "admin" });
  useThemeStore.getState().setMode("system");
});

describe("共享用户菜单", () => {
  it("在后台侧栏显示管理员账户入口", () => {
    render(
      <MemoryRouter initialEntries={["/admin"]}>
        <Routes>
          <Route path="/admin" element={<AdminLayout />}>
            <Route index element={<div>dashboard</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByRole("button", { name: "用户菜单" })).toHaveTextContent("admin");
    expect(screen.getByText("dashboard")).toBeInTheDocument();
  });

  it("调用登出接口后清理本地状态并跳转登录页", async () => {
    (api.post as any).mockResolvedValue({ data: { message: "已登出" } });
    render(
      <MemoryRouter initialEntries={["/admin"]}>
        <Routes>
          <Route path="/admin" element={<UserMenu dark />} />
          <Route path="/login" element={<div>login-page</div>} />
        </Routes>
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: "用户菜单" }));
    fireEvent.click(await screen.findByText("退出登录"));

    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/auth/logout"));
    expect(await screen.findByText("login-page")).toBeInTheDocument();
    expect(useAuthStore.getState().token).toBeNull();
  });

  it("登出接口失败时仍允许清理本地状态", async () => {
    (api.post as any).mockRejectedValue(new Error("offline"));
    render(
      <MemoryRouter initialEntries={["/admin"]}>
        <Routes>
          <Route path="/admin" element={<UserMenu />} />
          <Route path="/login" element={<div>login-page</div>} />
        </Routes>
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: "用户菜单" }));
    fireEvent.click(await screen.findByText("退出登录"));

    expect(await screen.findByText("login-page")).toBeInTheDocument();
    expect(useAuthStore.getState().token).toBeNull();
  });

  it("学习端用户菜单提供主题子菜单并持久化选择", async () => {
    render(
      <MemoryRouter>
        <UserMenu showThemeSettings />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: "用户菜单" }));
    const themeItem = await screen.findByText("主题：系统跟随");
    fireEvent.mouseEnter(themeItem);
    fireEvent.click(await screen.findByText("深色"));

    expect(useThemeStore.getState().mode).toBe("dark");
    expect(localStorage.getItem("ai-study-theme")).toBe("dark");
  });

  it("管理后台用户菜单不显示学习端主题选项", async () => {
    render(
      <MemoryRouter>
        <UserMenu dark />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: "用户菜单" }));
    expect(await screen.findByText("修改密码")).toBeInTheDocument();
    expect(screen.queryByText(/主题：/)).toBeNull();
  });
});
