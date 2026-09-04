import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import AISidebar from "../components/AISidebar";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const historyResponse = (messages: unknown[] = []) => ({
  ok: true,
  status: 200,
  json: async () => ({
    session_id: "column-session",
    column: { name: "Spring", current_video_name: "Spring 第1讲.mp4" },
    messages,
  }),
});

describe("AI 侧栏模型降级", () => {
  it("把助手 Markdown 渲染为书面内容并显示已保存的思考耗时", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(historyResponse([
      { role: "user", content: "# 用户输入保持原样" },
      {
        role: "assistant",
        content: "### 核心结论\n\n> 控制权交给容器。\n\n`ApplicationContext` 负责管理对象。\n\n| 对比 | IoC |\n| --- | --- |\n| 控制权 | 容器 |",
        thinking_ms: 1250,
      },
    ])));

    render(<AISidebar courseId="course-1" />);

    expect(await screen.findByRole("heading", { name: "核心结论" })).toBeInTheDocument();
    expect(screen.getByText("控制权交给容器。")).toBeInTheDocument();
    expect(screen.getByText("ApplicationContext")).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByText("# 用户输入保持原样")).toBeInTheDocument();
    expect(screen.getByText("思考耗时 1.3 秒")).toHaveAttribute("title", expect.stringContaining("首字"));
    expect(screen.getByRole("heading", { name: "核心结论" }).closest(".ai-message-row"))
      .toHaveClass("ai-message-row--assistant");
    expect(screen.getByText("# 用户输入保持原样")).toHaveClass("ai-user-message");
  });

  it("多行输入支持换行、中文组词，并只在普通 Enter 时发送", async () => {
    const bytes = new TextEncoder().encode(`data: ${JSON.stringify({ done: true })}\n\n`);
    let read = false;
    const fetchMock = vi.fn().mockImplementation((input: string) => {
      if (String(input).includes("column-session")) return Promise.resolve(historyResponse());
      return Promise.resolve({
        ok: true,
        body: { getReader: () => ({
          read: async () => {
            if (read) return { done: true, value: undefined };
            read = true;
            return { done: false, value: bytes };
          },
        }) },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AISidebar courseId="course-1" />);

    const textbox = await screen.findByRole("textbox");
    await waitFor(() => expect(textbox).not.toBeDisabled());
    expect(textbox.tagName).toBe("TEXTAREA");
    fireEvent.change(textbox, { target: { value: "第一行\n第二行" } });
    fireEvent.keyDown(textbox, { key: "Enter", shiftKey: true });
    fireEvent.keyDown(textbox, { key: "Enter", isComposing: true });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(textbox, { key: "Enter" });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const request = fetchMock.mock.calls[1][1] as RequestInit;
    expect(JSON.parse(String(request.body)).user_question).toBe("第一行\n第二行");
  });

  it("发送时自动携带当前播放位置和真实视频时长", async () => {
    const bytes = new TextEncoder().encode(
      `data: ${JSON.stringify({ session_id: "s-context" })}\n\n` +
        `data: ${JSON.stringify({ done: true, model_name: "qwen-plus" })}\n\n`,
    );
    let read = false;
    const fetchMock = vi.fn().mockImplementation((input: string) => {
      if (String(input).includes("column-session")) return Promise.resolve(historyResponse());
      return Promise.resolve({
        ok: true,
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
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AISidebar courseId="course-1" currentTime={42.5} videoDuration={600} />);
    await waitFor(() => expect(screen.getByRole("button", { name: /发\s*送/ })).not.toBeDisabled());
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "这个项目为什么这样设计？" } });
    fireEvent.click(screen.getByRole("button", { name: /发\s*送/ }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const request = fetchMock.mock.calls[1][1] as RequestInit;
    // A3：无主动引用时 start/end 是 Anchor 区间（null），播放位置走 current_time。
    // E3：不再调用 onContextConsumed（连续追问丢失引用的根因已移除）。
    expect(JSON.parse(String(request.body))).toMatchObject({
      course_id: "course-1",
      selected_subtitle: "",
      start_time: null,
      end_time: null,
      current_time: 42.5,
      video_duration: 600,
    });
    expect(JSON.parse(String(request.body))).not.toHaveProperty("onContextConsumed");
  });

  it("流中断时清空残片并显示下级模型完整答案", async () => {
    const sse = [
      { session_id: "s1" },
      { type: "delta", delta: "残片", model_name: "first", thinking_ms: 400 },
      { type: "attempt_reset", attempt_reset: true, model_name: "first" },
      { type: "fallback", fallback: true, to_model: "second", model_name: "second", notice: "正在切换备用模型" },
      { type: "delta", delta: "完整答案", model_name: "second", thinking_ms: 1700 },
      { type: "done", done: true, model_name: "second", thinking_ms: 1700 },
    ].map((item) => `data: ${JSON.stringify(item)}\n\n`).join("");
    const bytes = new TextEncoder().encode(sse);
    let read = false;
    vi.stubGlobal("fetch", vi.fn().mockImplementation((input: string) => {
      if (String(input).includes("column-session")) return Promise.resolve(historyResponse());
      return Promise.resolve({
        ok: true,
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
    }));

    render(<AISidebar courseId="course-1" />);
    await waitFor(() => expect(screen.getByRole("button", { name: /发\s*送/ })).not.toBeDisabled());
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "问题" } });
    fireEvent.click(screen.getByRole("button", { name: /发\s*送/ }));

    expect(await screen.findByText("完整答案")).toBeInTheDocument();
    expect(screen.queryByText("残片")).not.toBeInTheDocument();
    expect(screen.getByText("思考耗时 1.7 秒")).toBeInTheDocument();
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
    vi.stubGlobal("fetch", vi.fn().mockImplementation((input: string) => {
      if (String(input).includes("column-session")) return Promise.resolve(historyResponse());
      return Promise.resolve({
        ok: true,
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
    }));

    render(<AISidebar courseId="course-1" />);
    await waitFor(() => expect(screen.getByRole("button", { name: /发\s*送/ })).not.toBeDisabled());
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "问题" } });
    fireEvent.click(screen.getByRole("button", { name: /发\s*送/ }));

    expect(await screen.findByText("全部模型不可用")).toBeInTheDocument();
    expect(screen.queryByText("当前模型：last")).not.toBeInTheDocument();
  });

  it("进入专栏时恢复完整历史并显示提问来源", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      historyResponse([
        {
          role: "user",
          content: "IoC 是什么？",
          video_name: "005.Spring - IoC和DI.mp4",
          start_time: 125,
        },
        { role: "assistant", content: "IoC 是控制反转。", model_name: "qwen-plus" },
      ]),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<AISidebar courseId="spring-ioc-005" />);

    expect(await screen.findByText("IoC 是什么？")).toBeInTheDocument();
    expect(screen.getByText("IoC 是控制反转。")).toBeInTheDocument();
    expect(screen.getByText("005.Spring - IoC和DI.mp4 · 02:05")).toBeInTheDocument();
    expect(screen.getByText("专栏：Spring · 完整对话已保存")).toBeInTheDocument();
    expect(screen.queryByText(/最近 5 轮/)).not.toBeInTheDocument();
  });

  it("切换课程时立即清除旧专栏内容并等待新会话加载", async () => {
    let resolveSecond!: (value: ReturnType<typeof historyResponse>) => void;
    const secondResponse = new Promise<ReturnType<typeof historyResponse>>((resolve) => {
      resolveSecond = resolve;
    });
    const fetchMock = vi.fn().mockImplementation((input: string) => {
      if (String(input).includes("course-2")) return secondResponse;
      return Promise.resolve(historyResponse([{ role: "user", content: "旧专栏问题" }]));
    });
    vi.stubGlobal("fetch", fetchMock);
    const view = render(<AISidebar courseId="course-1" />);
    expect(await screen.findByText("旧专栏问题")).toBeInTheDocument();

    view.rerender(<AISidebar courseId="course-2" />);
    expect(screen.queryByText("旧专栏问题")).not.toBeInTheDocument();
    expect(screen.getByRole("textbox")).toBeDisabled();

    await act(async () => {
      resolveSecond(historyResponse([{ role: "user", content: "新专栏问题" }]));
    });
    expect(await screen.findByText("新专栏问题")).toBeInTheDocument();
  });

  it("清空接口失败时保留本地历史并提示失败", async () => {
    const fetchMock = vi.fn().mockImplementation((input: string, init?: RequestInit) => {
      if (init?.method === "POST") return Promise.resolve({ ok: false, status: 500 });
      return Promise.resolve(historyResponse([{ role: "user", content: "不能误删的问题" }]));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AISidebar courseId="course-1" />);
    expect(await screen.findByText("不能误删的问题")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /清空会话/ }));
    expect(await screen.findByText("清空失败")).toBeInTheDocument();
    expect(screen.getByText("不能误删的问题")).toBeInTheDocument();
  });
});
