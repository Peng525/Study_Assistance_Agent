import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import AISidebar from "../components/AISidebar";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("AI 侧栏模型降级", () => {
  it("发送时自动携带当前播放位置和真实视频时长", async () => {
    const bytes = new TextEncoder().encode(
      `data: ${JSON.stringify({ session_id: "s-context" })}\n\n` +
        `data: ${JSON.stringify({ done: true, model_name: "qwen-plus" })}\n\n`,
    );
    let read = false;
    const onContextConsumed = vi.fn();
    const fetchMock = vi.fn().mockResolvedValue({
      body: {
        getReader: () => ({
          read: async () => {
            if (read) return { done: true, value: undefined };
            read = true;
            return { done: false, value: bytes };
          },
        }),
      },
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AISidebar
        courseId="course-1"
        currentTime={42.5}
        videoDuration={600}
        onContextConsumed={onContextConsumed}
      />,
    );
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "这个项目为什么这样设计？" } });
    fireEvent.click(screen.getByRole("button", { name: /发\s*送/ }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const request = fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(request.body))).toMatchObject({
      course_id: "course-1",
      start_time: 42.5,
      end_time: 42.5,
      video_duration: 600,
    });
    expect(onContextConsumed).toHaveBeenCalledTimes(1);
  });

  it("流中断时清空残片并显示下级模型完整答案", async () => {
    const sse = [
      { session_id: "s1" },
      { type: "delta", delta: "残片", model_name: "first" },
      { type: "attempt_reset", attempt_reset: true, model_name: "first" },
      { type: "fallback", fallback: true, to_model: "second", model_name: "second", notice: "正在切换备用模型" },
      { type: "delta", delta: "完整答案", model_name: "second" },
      { type: "done", done: true, model_name: "second" },
    ].map((item) => `data: ${JSON.stringify(item)}\n\n`).join("");
    const bytes = new TextEncoder().encode(sse);
    let read = false;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      body: {
        getReader: () => ({
          read: async () => {
            if (read) return { done: true, value: undefined };
            read = true;
            return { done: false, value: bytes };
          },
        }),
      },
    }));

    render(<AISidebar courseId="course-1" />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "问题" } });
    fireEvent.click(screen.getByRole("button", { name: /发\s*送/ }));

    expect(await screen.findByText("完整答案")).toBeInTheDocument();
    expect(screen.queryByText("残片")).not.toBeInTheDocument();
    expect(screen.getByText("当前模型：second")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: /发\s*送/ })).not.toBeDisabled());
  });

  it("全链失败时不会把失败模型显示为当前模型", async () => {
    const sse = [
      { session_id: "s2" },
      { type: "fallback", fallback: true, to_model: "last", model_name: "last" },
      { type: "error", error: "全部模型不可用", model_name: "last" },
    ].map((item) => `data: ${JSON.stringify(item)}\n\n`).join("");
    const bytes = new TextEncoder().encode(sse);
    let read = false;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      body: {
        getReader: () => ({
          read: async () => {
            if (read) return { done: true, value: undefined };
            read = true;
            return { done: false, value: bytes };
          },
        }),
      },
    }));

    render(<AISidebar courseId="course-1" />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "问题" } });
    fireEvent.click(screen.getByRole("button", { name: /发\s*送/ }));

    expect(await screen.findByText("全部模型不可用")).toBeInTheDocument();
    expect(screen.queryByText("当前模型：last")).not.toBeInTheDocument();
  });
});
