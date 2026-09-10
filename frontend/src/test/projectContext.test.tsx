import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { Modal } from "antd";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ProjectContext from "../pages/admin/ProjectContext";
import { api } from "../api/client";

vi.mock("../api/client", () => ({ api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() } }));
vi.mock("../pages/admin/Materials", () => ({ default: ({ seriesId }: { seriesId: number }) => <div>专栏 {seriesId} 字幕工作区</div> }));
vi.mock("antd", async (importOriginal) => ({ ...(await importOriginal<any>()), message: { success: vi.fn(), info: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

const spring = { id: 2, name: "Spring", context_epoch: 1, video_count: 3, source: { id: 2, filename: "Spring.pptx", page_count: 38, outline_text: "", outline_status: "empty", outline_updated_at: null } };
function renderAt(path: string) {
  return render(<MemoryRouter initialEntries={[path]}><Routes><Route path="/admin/columns" element={<ProjectContext />} /><Route path="/admin/columns/:seriesId" element={<ProjectContext />} /></Routes></MemoryRouter>);
}
beforeEach(() => {
  vi.clearAllMocks();
  (api.get as any).mockImplementation((url: string) => Promise.resolve({ data: url === "/admin/columns" ? [spring] : [] }));
});

describe("专栏驱动信息架构", () => {
  it("专栏列表以内容容器展示课件、视频数和创建入口", async () => {
    renderAt("/admin/columns");
    expect(await screen.findByText("专栏是课件、视频、字幕和学习上下文的内容容器")).toBeInTheDocument();
    expect(screen.getByText("Spring.pptx")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /创建专栏/ })).toBeInTheDocument();
  });

  it("创建专栏调用独立 columns API", async () => {
    (api.post as any).mockResolvedValue({ data: {} });
    renderAt("/admin/columns");
    fireEvent.click(await screen.findByRole("button", { name: /创建专栏/ }));
    fireEvent.change(screen.getByLabelText("专栏名称"), { target: { value: "Java" } });
    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/admin/columns", { name: "Java" }));
  });

  it("专栏详情包含课件和视频两个职责区，并复用字幕工作区", async () => {
    renderAt("/admin/columns/2");
    expect(await screen.findByText("课件、视频和字幕都在当前专栏内管理")).toBeInTheDocument();
    expect(screen.getAllByText("Spring.pptx").length).toBeGreaterThan(0);
    expect(screen.getByText("当前课件")).toBeInTheDocument();
    expect(screen.getByText("专栏总大纲")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "课件" })).toHaveAttribute("aria-selected", "true");
    fireEvent.click(screen.getByRole("tab", { name: /视频/ }));
    expect(await screen.findByText("专栏 2 字幕工作区")).toBeInTheDocument();
  });

  it("更换 PPT 明确提示上下文世代语义", async () => {
    const spy = vi.spyOn(Modal, "confirm").mockReturnValue({ destroy: vi.fn(), update: vi.fn() } as any);
    const { container } = renderAt("/admin/columns/2");
    await screen.findAllByText("Spring.pptx");
    fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files: [new File(["ppt"], "Spring-v2.pptx")] } });
    await waitFor(() => expect(spy).toHaveBeenCalledWith(expect.objectContaining({ content: expect.stringContaining("切换上下文世代") })));
    spy.mockRestore();
  });

  it("待归类视频只提供归入专栏，不暴露字幕或课程知识操作", async () => {
    (api.get as any).mockImplementation((url: string) => Promise.resolve({
      data: url === "/admin/columns" ? [spring] : [{ course_id: "legacy-001", status: "ready", subtitle_status: "ready", review_state: "unreviewed", series_id: null }],
    }));
    renderAt("/admin/columns");
    fireEvent.click(await screen.findByRole("tab", { name: "待归类视频（1）" }));
    expect(await screen.findByText("legacy-001")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "归入专栏" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /生成字幕|查看字幕|配置课程知识/ })).toBeNull();
  });

  it("stale 只按后端明确状态展示，旧大纲只读且提供重新生成", async () => {
    const stale = { ...spring, source: { ...spring.source, outline_text: "# 旧大纲", outline_status: "stale" } };
    (api.get as any).mockImplementation((url: string) => Promise.resolve({ data: url === "/admin/columns" ? [stale] : [] }));
    renderAt("/admin/columns/2");
    expect(await screen.findByText("旧大纲基于旧课件，仅供查看，不会进入模型上下文。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查看旧大纲" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新生成总大纲" })).toBeInTheDocument();
  });

  it("草稿在 Drawer 编辑，只有保存并启用时才提交", async () => {
    const draft = { ...spring, source: { ...spring.source, outline_text: "# 草稿", outline_status: "draft" } };
    (api.get as any).mockImplementation((url: string) => Promise.resolve({ data: url === "/admin/columns" ? [draft] : [] }));
    (api.put as any).mockResolvedValue({ data: {} });
    renderAt("/admin/columns/2");
    fireEvent.click(await screen.findByRole("button", { name: "审核草稿" }));
    const editor = await screen.findByLabelText("专栏总大纲");
    fireEvent.change(editor, { target: { value: "# 已审核大纲" } });
    expect(api.put).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "保存并启用" }));
    await waitFor(() => expect(api.put).toHaveBeenCalledWith(
      "/admin/project-context/sources/2/outline",
      { outline_text: "# 已审核大纲" },
    ));
  });
});
