import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import Login from "../pages/Login";
import { api } from "../api/client";
import { useAuthStore } from "../store/auth";

// mock api 客户端
vi.mock("../api/client", () => ({
  api: {
    post: vi.fn(),
  },
}));

// mock antd message
vi.mock("antd", async (importOriginal) => {
  const actual = await importOriginal<any>();
  return {
    ...actual,
    message: { success: vi.fn(), error: vi.fn() },
  };
});

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  useAuthStore.setState({ token: null, user: null });
});

describe("登录页渲染", () => {
  it("渲染登录表单", () => {
    render(
      <MemoryRouter initialEntries={["/login"]}>
        <Routes>
          <Route path="/login" element={<Login />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByPlaceholderText("用户名")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("密码")).toBeInTheDocument();
    expect(screen.getByText("登 录")).toBeInTheDocument();
  });
});

describe("登录后按 role 跳转", () => {
  it("admin 登录后跳转到 /admin", async () => {
    (api.post as any).mockResolvedValueOnce({
      data: {
        access_token: "fake-jwt",
        user: { user_id: 1, username: "admin", role: "admin" },
      },
    });
    render(
      <MemoryRouter initialEntries={["/login"]}>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/admin" element={<div data-testid="landing">admin-landing</div>} />
          <Route path="/" element={<div data-testid="landing">user-landing</div>} />
        </Routes>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByText("登 录"));
    await waitFor(() => {
      expect(screen.getByTestId("landing")).toHaveTextContent("admin-landing");
    });
  });

  it("user 登录后跳转到 /", async () => {
    (api.post as any).mockResolvedValueOnce({
      data: {
        access_token: "fake-jwt",
        user: { user_id: 2, username: "user25", role: "user" },
      },
    });
    render(
      <MemoryRouter initialEntries={["/login"]}>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/admin" element={<div data-testid="landing">admin-landing</div>} />
          <Route path="/" element={<div data-testid="landing">user-landing</div>} />
        </Routes>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByText("登 录"));
    await waitFor(() => {
      expect(screen.getByTestId("landing")).toHaveTextContent("user-landing");
    });
  });
});