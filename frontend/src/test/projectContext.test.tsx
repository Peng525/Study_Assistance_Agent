import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ProjectContext from "../pages/admin/ProjectContext";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

vi.mock("antd", async (importOriginal) => {
  const actual = await importOriginal<any>();
  return {
    ...actual,
    message: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
  };
});

const state = {
  project: { project_key: "default-study-project", name: "默认学习项目" },
  sources: [
    {
      id: 1,
      filename: "Spring.pptx",
      format: "pptx",
      sha256: "a".repeat(64),
      status: "active",
      page_count: 38,
    },
  ],
  published: {
    id: 1,
    version: 1,
    summary_text: "已审核项目背景",
    status: "published",
    is_stale: true,
  },
  draft: {
    id: 2,
    version: 2,
    summary_text: "待审核项目背景",
    status: "draft",
    is_stale: false,
  },
  material_count: 2,
  videos: [
    {
      course_id: "spring-video-1",
      video_name: "Spring IoC.mp4",
      course_type: "practice",
      source_id: 1,
      source_filename: "Spring.pptx",
      page_start: 9,
      page_end: 12,
      knowledge_text: "IoC 是控制反转，DI 是依赖注入。",
      knowledge_filename: "course-knowledge.md",
      outline_text: "",
      outline_status: "empty",
      subtitle_included: false,
      legacy_context: false,
    },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  (api.get as any).mockResolvedValue({ data: state });
  (api.put as any).mockResolvedValue({ data: {} });
});

describe("项目背景管理", () => {
  it("保留旧版摘要编辑能力且取消 2000 字输入限制", async () => {
    render(<ProjectContext />);

    expect(await screen.findByText("旧版专栏公共摘要（兼容）")).toBeInTheDocument();
    expect(screen.getByText("已审核项目背景")).toBeInTheDocument();
    const draft = screen.getByDisplayValue("待审核项目背景");
    expect(draft).toBeInTheDocument();
    expect(draft).not.toHaveAttribute("maxlength");
    expect(screen.getByText("Spring.pptx")).toBeInTheDocument();

    fireEvent.change(screen.getByDisplayValue("待审核项目背景"), {
      target: { value: "管理员修订后的项目背景" },
    });
    fireEvent.click(screen.getByRole("button", { name: /保存草稿/ }));

    await waitFor(() => {
      expect(api.put).toHaveBeenCalledWith("/admin/project-context/summary/draft", {
        version_id: 2,
        summary_text: "管理员修订后的项目背景",
      });
    });
  });

  it("资料超过 AI 预算时可以创建人工摘要草稿", async () => {
    (api.get as any).mockResolvedValue({ data: { ...state, draft: null } });
    render(<ProjectContext />);

    fireEvent.click(await screen.findByRole("button", { name: "新建人工草稿" }));

    await waitFor(() => {
      expect(api.put).toHaveBeenCalledWith(
        "/admin/project-context/summary/draft",
        expect.objectContaining({
          version_id: null,
          summary_text: expect.stringContaining("# 项目定位"),
        }),
      );
    });
  });

  it("生成 AI 草稿时不使用全局 30 秒请求超时", async () => {
    (api.post as any).mockResolvedValue({ data: {} });
    render(<ProjectContext />);

    fireEvent.click(await screen.findByRole("button", { name: /AI 生成草稿/ }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        "/admin/project-context/summary/generate",
        undefined,
        { timeout: 0 },
      );
    });
  });

  it("视频知识弹窗左右显示课件文本和空大纲生成入口", async () => {
    (api.get as any).mockImplementation((url: string) => {
      if (url.includes("/sources/1/pages")) {
        return Promise.resolve({
          data: {
            pages: [
              { page: 9, title: "IoC", text: "【第9页 IoC】\nIoC 是控制反转" },
              { page: 10, title: "DI", text: "【第10页 DI】\nDI 是依赖注入" },
            ],
          },
        });
      }
      return Promise.resolve({ data: state });
    });
    (api.post as any).mockResolvedValue({
      data: {
        video: {
          ...state.videos[0],
          outline_text: "# IoC 与 DI",
          outline_status: "draft",
        },
      },
    });
    render(<ProjectContext />);

    fireEvent.click(await screen.findByRole("button", { name: "配置课程知识" }));
    expect(await screen.findByText("课件文本（左）")).toBeInTheDocument();
    expect(screen.getByText("视频大纲（右）")).toBeInTheDocument();
    expect(screen.getByText("还没有大纲，是否根据左侧课程文本生成？")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "需要，生成大纲" }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        "/admin/project-context/videos/spring-video-1/outline/generate",
        undefined,
        { timeout: 0 },
      );
    });
  });

  it("改选未保存页码时阻止基于旧课程文本生成大纲", async () => {
    (api.get as any).mockImplementation((url: string) => {
      if (url.includes("/sources/1/pages")) {
        return Promise.resolve({
          data: {
            pages: [
              { page: 9, title: "IoC", text: "【第9页 IoC】\nIoC 是控制反转" },
              { page: 10, title: "DI", text: "【第10页 DI】\nDI 是依赖注入" },
              { page: 11, title: "容器", text: "【第11页 容器】\n容器管理组件" },
              { page: 12, title: "总结", text: "【第12页 总结】\nIoC 与 DI" },
            ],
          },
        });
      }
      return Promise.resolve({ data: state });
    });
    render(<ProjectContext />);

    fireEvent.click(await screen.findByRole("button", { name: "配置课程知识" }));
    const generateButton = await screen.findByRole("button", { name: "需要，生成大纲" });
    expect(generateButton).toBeEnabled();

    fireEvent.change(screen.getByLabelText("PPT 起始页"), { target: { value: "10" } });

    await waitFor(() => {
      expect(screen.getByText("当前是未保存的新页预览；请先生成课程文本，再操作右侧大纲")).toBeInTheDocument();
      expect(generateButton).toBeDisabled();
    });
    expect(api.post).not.toHaveBeenCalledWith(
      "/admin/project-context/videos/spring-video-1/outline/generate",
      undefined,
      { timeout: 0 },
    );
  });

  it("切换到尚未加载完成的新 PPT 时立即阻止使用旧文本生成大纲", async () => {
    const stateWithSecondPpt = {
      ...state,
      sources: [
        ...state.sources,
        {
          id: 2,
          filename: "Spring-Advanced.pptx",
          format: "pptx",
          sha256: "b".repeat(64),
          status: "active",
          page_count: 20,
        },
      ],
    };
    (api.get as any).mockImplementation((url: string) => {
      if (url.includes("/sources/1/pages")) {
        return Promise.resolve({
          data: {
            pages: [
              { page: 9, title: "IoC", text: "【第9页 IoC】\nIoC 是控制反转" },
              { page: 12, title: "总结", text: "【第12页 总结】\nIoC 与 DI" },
            ],
          },
        });
      }
      if (url.includes("/sources/2/pages")) return new Promise(() => undefined);
      return Promise.resolve({ data: stateWithSecondPpt });
    });
    render(<ProjectContext />);

    fireEvent.click(await screen.findByRole("button", { name: "配置课程知识" }));
    const generateButton = await screen.findByRole("button", { name: "需要，生成大纲" });
    expect(generateButton).toBeEnabled();

    fireEvent.mouseDown(screen.getAllByRole("combobox")[1]);
    fireEvent.click(await screen.findByText("Spring-Advanced.pptx（20 页）"));

    await waitFor(() => {
      expect(generateButton).toBeDisabled();
      expect(screen.getByText("当前是未保存的新页预览；请先生成课程文本，再操作右侧大纲")).toBeInTheDocument();
    });
    expect(api.post).not.toHaveBeenCalledWith(
      "/admin/project-context/videos/spring-video-1/outline/generate",
      undefined,
      { timeout: 0 },
    );
  });
});
