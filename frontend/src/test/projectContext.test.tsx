import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { Modal } from "antd";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ProjectContext from "../pages/admin/ProjectContext";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock("antd", async (importOriginal) => {
  const actual = await importOriginal<any>();
  return { ...actual, message: { success: vi.fn(), info: vi.fn(), error: vi.fn(), warning: vi.fn() } };
});

const source = {
  id: 1, filename: "Spring.pptx", column_name: "Spring", format: "pptx",
  sha256: "a".repeat(64), page_count: 38, upload_status: "uploaded",
  outline_text: "", outline_status: "empty",
};
const video = {
  course_id: "spring-video-1", video_name: "Spring IoC.mp4", course_type: "practice",
  source_id: 1, source_filename: "Spring.pptx", page_start: 9, page_end: 10,
  knowledge_text: "IoC 是控制反转", knowledge_status: "ready",
};
const state = {
  project: { project_key: "default-study-project", name: "默认学习项目" },
  sources: [source], videos: [video],
};

function renderAt(path: string) {
  return render(<MemoryRouter initialEntries={[path]}><Routes>
    <Route path="/admin/columns/courseware" element={<ProjectContext />} />
    <Route path="/admin/columns" element={<ProjectContext />} />
    <Route path="/admin/columns/:sourceId" element={<ProjectContext />} />
    <Route path="/admin/columns/:sourceId/videos/:courseId" element={<ProjectContext />} />
  </Routes></MemoryRouter>);
}

beforeEach(() => {
  vi.clearAllMocks();
  (api.get as any).mockImplementation((url: string) => Promise.resolve(url.includes("/pages")
    ? { data: { pages: [{ page: 9, title: "IoC", text: "【第9页】\nIoC" }, { page: 10, title: "DI", text: "【第10页】\nDI" }] } }
    : { data: state }));
  (api.put as any).mockResolvedValue({ data: {} });
});

describe("专栏化课件管理", () => {
  it("课件页合并为一个选择并上传入口，并展示上传与总大纲状态", async () => {
    renderAt("/admin/columns/courseware");
    expect(await screen.findByRole("button", { name: /选择并上传课件/ })).toBeInTheDocument();
    expect(screen.getByText("已上传")).toBeInTheDocument();
    expect(screen.getByText("未生成")).toBeInTheDocument();
    expect(screen.queryByText("项目背景")).not.toBeInTheDocument();
    expect(screen.queryByText(/视频大纲/)).not.toBeInTheDocument();
  });

  it("专栏视频页可人工编辑课程类型且只显示课程文本状态", async () => {
    renderAt("/admin/columns/1");
    expect(await screen.findByText("课程类型只用于管理分类，不影响课件上下文是否发送。")).toBeInTheDocument();
    expect(screen.getByText("已就绪")).toBeInTheDocument();
    expect(screen.queryByText(/视频大纲/)).not.toBeInTheDocument();
    fireEvent.mouseDown(screen.getByRole("combobox"));
    fireEvent.click(await screen.findByText("理论/通用"));
    await waitFor(() => expect(api.put).toHaveBeenCalledWith(
      "/admin/project-context/videos/spring-video-1/course-type", { course_type: "theory" },
    ));
  });

  it("视频详情左右展示页原文与整份 PPT 总大纲，并从课件生成总大纲", async () => {
    (api.post as any).mockResolvedValue({ data: { source: { ...source, outline_text: "# 总大纲", outline_status: "draft" } } });
    renderAt("/admin/columns/1/videos/spring-video-1");
    expect(await screen.findByText("当前视频 PPT 页原文")).toBeInTheDocument();
    expect(screen.getByText("整份 PPT 专栏总大纲")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "AI 生成草稿" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      "/admin/project-context/sources/1/outline/generate", undefined, { timeout: 0 },
    ));
    const editor = await screen.findByLabelText("专栏总大纲");
    expect(editor).not.toHaveAttribute("maxlength");
  });

  it("覆盖课件时先处理总大纲选择，再提示受影响视频", async () => {
    const confirms: any[] = [];
    const confirmSpy = vi.spyOn(Modal, "confirm").mockImplementation((config: any) => {
      confirms.push(config);
      return { destroy: vi.fn(), update: vi.fn() } as any;
    });
    const infoSpy = vi.spyOn(Modal, "info").mockReturnValue({ destroy: vi.fn(), update: vi.fn() } as any);
    (api.put as any).mockResolvedValue({
      data: { source: { ...source, outline_status: "stale" }, affected_video_count: 1, unchanged: false },
    });
    const { container } = renderAt("/admin/columns/courseware");
    await screen.findByText("Spring.pptx");
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["ppt"], "Spring.pptx")] } });
    await waitFor(() => expect(confirms).toHaveLength(1));

    const upload = confirms[0].onOk();
    await waitFor(() => expect(confirms).toHaveLength(2));
    expect(infoSpy).not.toHaveBeenCalled();
    confirms[1].onCancel();
    await upload;
    expect(infoSpy).toHaveBeenCalledWith(expect.objectContaining({ title: "关联视频课程文本已失效" }));
    confirmSpy.mockRestore();
    infoSpy.mockRestore();
  });

  it("视频详情明确 stale 总大纲仅供参考且不进入问答", async () => {
    (api.get as any).mockImplementation((url: string) => Promise.resolve(url.includes("/pages")
      ? { data: { pages: [{ page: 9, title: "IoC", text: "【第9页】\nIoC" }] } }
      : { data: { ...state, sources: [{ ...source, outline_text: "旧大纲", outline_status: "stale" }] } }));
    renderAt("/admin/columns/1/videos/spring-video-1");
    expect(await screen.findByText("课件已更新，旧大纲仅供参考，不会进入问答。")).toBeInTheDocument();
  });
});
