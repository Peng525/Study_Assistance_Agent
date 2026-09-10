import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { message } from "antd";
import VideoKnowledge from "../pages/admin/VideoKnowledge";
import { api } from "../api/client";

vi.mock("../api/client", () => ({ api: { get: vi.fn(), put: vi.fn() } }));
vi.mock("antd", async (importOriginal) => ({
  ...(await importOriginal<any>()),
  message: { success: vi.fn(), error: vi.fn() },
}));

const series = { id: 2, name: "Spring", source: { id: 20 } };
const video = {
  course_id: "spring-ioc-005",
  video_name: "IoC 与 DI",
  series_id: 2,
  course_type: "theory",
  page_start: 2,
  page_end: 3,
  knowledge_text: "旧课程文本",
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/admin/columns/2/videos/spring-ioc-005"]}>
      <Routes>
        <Route path="/admin/columns/:seriesId/videos/:courseId" element={<VideoKnowledge />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  (api.get as any).mockImplementation((url: string) => Promise.resolve({
    data: url === "/admin/columns"
      ? [series]
      : url === "/admin/project-context"
        ? { videos: [video] }
        : { pages: [{ page: 2, text: "第二页" }, { page: 3, text: "第三页" }] },
  }));
});

describe("专栏视频课程知识", () => {
  it("课程类型保存失败时提示错误且不产生未处理拒绝", async () => {
    (api.put as any).mockRejectedValue({ response: { data: { detail: "类型更新失败" } } });
    renderPage();

    fireEvent.mouseDown(await screen.findByRole("combobox", { name: "课程类型" }));
    fireEvent.click(await screen.findByText("实战/案例"));

    await waitFor(() => expect(message.error).toHaveBeenCalledWith("类型更新失败"));
  });

  it("按当前专栏课件与页区间生成课程文本", async () => {
    (api.put as any).mockResolvedValue({ data: {} });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "生成课程文本" }));

    await waitFor(() => expect(api.put).toHaveBeenCalledWith(
      "/admin/project-context/videos/spring-ioc-005/knowledge",
      { source_id: 20, page_start: 2, page_end: 3, course_type: "theory" },
    ));
  });
});
