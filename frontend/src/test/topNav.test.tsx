import { fireEvent, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import TopNav from "../components/TopNav";
import { useAuthStore } from "../store/auth";

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>;
}

function renderNav(initialEntry: string, props: ComponentProps<typeof TopNav> = {}) {
  useAuthStore.setState({
    token: "token",
    user: { user_id: 2, username: "user25", role: "user" },
  });

  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <TopNav {...props} />
      <Routes>
        <Route path="*" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("学习端顶部导航", () => {
  it("将首页和课程列表渲染为带选中态的标签导航", () => {
    renderNav("/courses");

    expect(screen.getByRole("navigation", { name: "学习导航" })).toHaveClass("top-nav__tabs");
    expect(screen.getByRole("link", { name: "首页" })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("link", { name: "课程列表" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "课程列表" })).toHaveClass("top-nav__tab--active");
  });

  it("播放页保持课程列表标签选中", () => {
    renderNav("/course/demo-course");

    expect(screen.getByRole("link", { name: "课程列表" })).toHaveAttribute("aria-current", "page");
  });

  it("仅在播放页传入开关时将 AI 对话按钮放在用户菜单左侧", () => {
    const toggle = vi.fn();
    const { container } = renderNav("/course/demo-course", {
      aiExpanded: true,
      onToggleAI: toggle,
    });

    const aiButton = screen.getByRole("button", { name: "收起 AI 对话" });
    const userMenu = screen.getByRole("button", { name: "用户菜单" });
    expect(aiButton).toHaveAttribute("aria-pressed", "true");
    expect(
      aiButton.compareDocumentPosition(userMenu) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    fireEvent.click(aiButton);
    expect(toggle).toHaveBeenCalledOnce();
    expect(container.querySelector(".top-nav__ai-toggle--active")).toBe(aiButton);
  });

  it("普通学习页面不显示 AI 对话按钮", () => {
    renderNav("/");

    expect(screen.queryByRole("button", { name: /AI 对话/ })).toBeNull();
  });

  it("回车搜索时进入带编码关键词的课程列表", () => {
    renderNav("/");

    const input = screen.getByPlaceholderText("搜索课程 / 主题");
    fireEvent.change(input, { target: { value: "人工智能 入门" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter", charCode: 13 });

    expect(screen.getByTestId("location")).toHaveTextContent(
      "/courses?q=%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD%20%E5%85%A5%E9%97%A8",
    );
  });
});
