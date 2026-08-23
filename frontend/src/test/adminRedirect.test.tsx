import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import AdminRedirectIfNeeded from "../components/AdminRedirectIfNeeded";
import { useAuthStore } from "../store/auth";

function setRole(role: "admin" | "user" | null) {
  // 直接写 localStorage + 重置 store
  if (role) {
    localStorage.setItem("ai-study-user", JSON.stringify({ user_id: 1, username: "x", role }));
    localStorage.setItem("ai-study-token", "fake");
  } else {
    localStorage.removeItem("ai-study-user");
    localStorage.removeItem("ai-study-token");
  }
  // 重新初始化 store
  useAuthStore.setState({ token: role ? "fake" : null, user: role ? { user_id: 1, username: "x", role } : null });
}

beforeEach(() => {
  localStorage.clear();
});

describe("AdminRedirectIfNeeded 守卫", () => {
  it("user 访问学习端路径正常渲染 children", () => {
    setRole("user");
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route path="/" element={<AdminRedirectIfNeeded><div data-testid="content">user-home</div></AdminRedirectIfNeeded>} />
          <Route path="/admin" element={<div data-testid="admin">admin-page</div>} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByTestId("content")).toBeInTheDocument();
    expect(screen.queryByTestId("admin")).not.toBeInTheDocument();
  });

  it("admin 访问 / 被重定向到 /admin", () => {
    setRole("admin");
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route path="/" element={<AdminRedirectIfNeeded><div data-testid="content">should-not-show</div></AdminRedirectIfNeeded>} />
          <Route path="/admin" element={<div data-testid="admin">admin-page</div>} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByTestId("admin")).toBeInTheDocument();
    expect(screen.queryByTestId("content")).not.toBeInTheDocument();
  });

  it("admin 访问 /courses 被重定向到 /admin", () => {
    setRole("admin");
    render(
      <MemoryRouter initialEntries={["/courses"]}>
        <Routes>
          <Route path="/courses" element={<AdminRedirectIfNeeded><div data-testid="content">courses</div></AdminRedirectIfNeeded>} />
          <Route path="/admin" element={<div data-testid="admin">admin-page</div>} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByTestId("admin")).toBeInTheDocument();
  });

  it("admin 访问 /course/abc 被重定向到 /admin", () => {
    setRole("admin");
    render(
      <MemoryRouter initialEntries={["/course/abc"]}>
        <Routes>
          <Route path="/course/:courseId" element={<AdminRedirectIfNeeded><div data-testid="content">player</div></AdminRedirectIfNeeded>} />
          <Route path="/admin" element={<div data-testid="admin">admin-page</div>} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByTestId("admin")).toBeInTheDocument();
  });
});