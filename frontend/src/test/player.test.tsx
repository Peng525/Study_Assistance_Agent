import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import Player from "../pages/Player";
import { useAuthStore } from "../store/auth";

const { artInstances, MockArtplayer } = vi.hoisted(() => {
  class ArtplayerMock {
    options: Record<string, unknown>;
    currentTime = 0;
    duration = 600;
    seek = 0;
    pause = vi.fn();
    destroy = vi.fn();
    // CC 控件注入所需（E2）：controls.add 立即调用 mounted 回调并传入合成元素，
    // 这样 Player 的 ccControlElRef 在测试中也能拿到非空元素。
    controlOptions: any[] = [];
    controls = {
      add: vi.fn((opt: any) => {
        this.controlOptions.push(opt);
        opt.mounted?.(document.createElement("div"));
        return document.createElement("div");
      }),
      remove: vi.fn(),
      update: vi.fn(),
    };
    private handlers = new Map<string, Array<(...args: unknown[]) => void>>();

    constructor(options: Record<string, unknown>) {
      this.options = options;
      artInstances.push(this);
    }

    on(event: string, handler: (...args: unknown[]) => void) {
      this.handlers.set(event, [...(this.handlers.get(event) || []), handler]);
    }

    emit(event: string, ...args: unknown[]) {
      this.handlers.get(event)?.forEach((handler) => handler(...args));
    }
  }

  const artInstances: ArtplayerMock[] = [];
  return { artInstances, MockArtplayer: ArtplayerMock };
});

vi.mock("artplayer", () => ({ default: MockArtplayer }));
vi.mock("antd", async (importOriginal) => {
  const actual = await importOriginal<any>();
  return {
    ...actual,
    message: { info: vi.fn(), error: vi.fn(), success: vi.fn() },
  };
});

const aiPlaceholder = "例如：我现在看到在创建 subagent，我想知道创建 subagent 应该怎么做。";

function mockMediaFetch(options: { subtitle?: string; ticketOk?: boolean; chatOk?: boolean } = {}) {
  const { subtitle, ticketOk = true, chatOk = false } = options;
  const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/subtitle")) {
      return Promise.resolve({
        ok: subtitle !== undefined,
        status: subtitle !== undefined ? 200 : 404,
        text: () => Promise.resolve(subtitle || ""),
      } as Response);
    }
    if (url.endsWith("/playback-ticket")) {
      return Promise.resolve({
        ok: ticketOk,
        status: ticketOk ? 200 : 404,
        json: () => Promise.resolve({
          url: "/api/materials/course-1/video-playback?ticket=scoped-ticket",
        }),
      } as Response);
    }
    if (url.endsWith("/api/chat/stream") && chatOk) {
      const bytes = new TextEncoder().encode('data: {"done":true,"model_name":"qwen-plus"}\n\n');
      let consumed = false;
      return Promise.resolve({
        ok: true,
        body: {
          getReader: () => ({
            read: () => {
              if (consumed) return Promise.resolve({ done: true, value: undefined });
              consumed = true;
              return Promise.resolve({ done: false, value: bytes });
            },
          }),
        },
      } as unknown as Response);
    }
    return Promise.reject(new Error(`unexpected request: ${url}`));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderPlayer() {
  useAuthStore.getState().login("token", { user_id: 2, username: "user25", role: "user" });
  return render(
    <MemoryRouter initialEntries={["/course/course-1"]}>
      <Routes>
        <Route path="/course/:courseId" element={<Player />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  artInstances.length = 0;
  localStorage.clear();
});

describe("播放器页面", () => {
  it("用已鉴权请求换取播放票据，并交给播放器原生加载", async () => {
    const fetchMock = mockMediaFetch();
    const { container, unmount } = renderPlayer();

    await waitFor(() => expect(artInstances).toHaveLength(1));
    const art = artInstances[0];
    expect(art.options.url).toBe(
      "/api/materials/course-1/video-playback?ticket=scoped-ticket",
    );
    expect(art.options).toMatchObject({
      volume: 0.7,
      playbackRate: true,
      setting: true,
      hotkey: true,
      fullscreenWeb: true,
      fullscreen: true,
    });
    expect(container.querySelector(".player-video-frame")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/materials/course-1/playback-ticket",
      expect.objectContaining({
        method: "POST",
        headers: { Authorization: "Bearer token" },
        signal: expect.any(AbortSignal),
      }),
    );

    unmount();
    expect(art.destroy).toHaveBeenCalledWith(false);
  });

  it("无字幕时保持静默且视频与 AI 对话仍可使用", async () => {
    mockMediaFetch();
    const { container } = renderPlayer();

    await waitFor(() => expect(artInstances).toHaveLength(1));
    expect(screen.queryByText(/字幕交互降级|字幕生成中|字幕不可用/)).toBeNull();
    expect(container.querySelector(".subtitle-overlay")).toBeNull();
    expect(screen.queryByText("返回")).toBeNull();

    fireEvent.contextMenu(screen.getByTestId("video-surface"));
    fireEvent.click(await screen.findByText("以当前播放时间点向 AI 提问"));
    const input = await screen.findByPlaceholderText(aiPlaceholder);
    // E3：不再预填模板文字，输入框为空；L3 不创建 Anchor（无引用卡片）
    expect(input).toHaveValue("");
    expect(screen.queryByTestId("citation-card")).toBeNull();

    fireEvent.change(input, { target: { value: "保留这段对话" } });
    fireEvent.click(screen.getByRole("button", { name: "收起 AI 对话" }));
    expect(container.querySelector(".player-ai-panel")).toHaveAttribute("aria-hidden", "true");
    fireEvent.click(screen.getByRole("button", { name: "展开 AI 对话" }));
    expect(input).toHaveValue("保留这段对话");
    expect(container.querySelector(".player-ai-panel")).toHaveAttribute("aria-hidden", "false");
  });

  it("AI 对话默认收起、展开后默认 40% 并支持拖拽与记忆", async () => {
    vi.stubGlobal("PointerEvent", MouseEvent);
    mockMediaFetch();
    const { container } = renderPlayer();
    await waitFor(() => expect(artInstances).toHaveLength(1));

    const panel = container.querySelector(".player-ai-panel") as HTMLElement;
    const workspace = container.querySelector(".player-workspace") as HTMLElement;

    // E1：默认收起（AC-SUB-001）
    expect(panel.style.width).toBe("0px");
    expect(panel).toHaveAttribute("aria-hidden", "true");

    // 展开后默认 40%
    fireEvent.click(screen.getByRole("button", { name: "展开 AI 对话" }));
    expect(panel.style.width).toBe("40%");
    expect(panel).toHaveAttribute("aria-hidden", "false");

    const splitter = screen.getByRole("separator", { name: "调整 AI 对话宽度" });
    workspace.getBoundingClientRect = () => ({
      width: 1000, right: 1000, left: 0, top: 0, bottom: 600, height: 600,
      x: 0, y: 0, toJSON: () => ({}),
    });
    (splitter as any).setPointerCapture = vi.fn();
    (splitter as any).releasePointerCapture = vi.fn();

    // 向左拖到远超上限的坐标（理论值 90%），验证被 clamp 到 MAX_AI_WIDTH(50)
    fireEvent.pointerDown(splitter, { pointerId: 1, clientX: 500 });
    fireEvent.pointerMove(splitter, { pointerId: 1, clientX: 100 });
    fireEvent.pointerUp(splitter, { pointerId: 1, clientX: 100 });
    expect(panel.style.width).toBe("50%");
    expect(localStorage.getItem("ai-study-sidebar-width")).toBe("50");

    fireEvent.keyDown(splitter, { key: "ArrowRight" });
    expect(panel.style.width).toBe("48%");
    expect(localStorage.getItem("ai-study-sidebar-width")).toBe("48");

    // 拖到区间内的中间值（理论值 40%），验证非边界值也能正常保存
    fireEvent.pointerDown(splitter, { pointerId: 2, clientX: 520 });
    fireEvent.pointerMove(splitter, { pointerId: 2, clientX: 600 });
    fireEvent.pointerCancel(splitter, { pointerId: 2 });
    expect(panel.style.width).toBe("40%");
    expect(localStorage.getItem("ai-study-sidebar-width")).toBe("40");
    expect(splitter).not.toHaveClass("player-splitter--active");

    fireEvent.click(screen.getByRole("button", { name: "收起 AI 对话" }));
    expect(panel.style.width).toBe("0px");
    fireEvent.click(screen.getByRole("button", { name: "展开 AI 对话" }));
    expect(panel.style.width).toBe("40%");
  });

  it("AI 侧边栏向左拖到底也不超过半屏，视频区始终保留一半", async () => {
    vi.stubGlobal("PointerEvent", MouseEvent);
    mockMediaFetch();
    const { container } = renderPlayer();
    await waitFor(() => expect(artInstances).toHaveLength(1));

    const panel = container.querySelector(".player-ai-panel") as HTMLElement;
    const workspace = container.querySelector(".player-workspace") as HTMLElement;
    workspace.getBoundingClientRect = () => ({
      width: 1000, right: 1000, left: 0, top: 0, bottom: 600, height: 600,
      x: 0, y: 0, toJSON: () => ({}),
    });
    // 先展开（默认收起），splitter 才渲染
    fireEvent.click(screen.getByRole("button", { name: "展开 AI 对话" }));
    const splitter = screen.getByRole("separator", { name: "调整 AI 对话宽度" });
    (splitter as any).setPointerCapture = vi.fn();
    (splitter as any).releasePointerCapture = vi.fn();

    // 一路拖到最左侧（理论值 100%），仍应停在 50%
    fireEvent.pointerDown(splitter, { pointerId: 3, clientX: 900 });
    fireEvent.pointerMove(splitter, { pointerId: 3, clientX: 0 });
    fireEvent.pointerUp(splitter, { pointerId: 3, clientX: 0 });
    expect(panel.style.width).toBe("50%");
    expect(splitter).toHaveAttribute("aria-valuemax", "50");
  });

  it("有效字幕只在当前时间命中 cue 时显示", async () => {
    mockMediaFetch({
      subtitle: "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n当前字幕\n",
    });
    const { container } = renderPlayer();

    await waitFor(() => expect(artInstances).toHaveLength(1));
    expect(container.querySelector(".subtitle-overlay")).toBeNull();

    act(() => {
      artInstances[0].currentTime = 2;
      artInstances[0].emit("video:timeupdate");
    });
    expect(await screen.findByText("当前字幕")).toBeInTheDocument();

    act(() => {
      artInstances[0].currentTime = 4;
      artInstances[0].emit("video:timeupdate");
    });
    await waitFor(() => expect(container.querySelector(".subtitle-overlay")).toBeNull());
  });

  it("真实视频请求失败时显示独立错误且不创建播放器", async () => {
    mockMediaFetch({ ticketOk: false });
    renderPlayer();

    expect(await screen.findByRole("alert")).toHaveTextContent("视频加载失败，请稍后重试");
    expect(artInstances).toHaveLength(0);
    expect(screen.queryByText(/字幕交互降级/)).toBeNull();
  });

  it("发送提问时自动携带播放器当前时间和真实时长", async () => {
    const fetchMock = mockMediaFetch({ chatOk: true });
    renderPlayer();

    await waitFor(() => expect(artInstances).toHaveLength(1));
    act(() => {
      artInstances[0].duration = 600;
      artInstances[0].emit("ready");
      artInstances[0].currentTime = 75.5;
      artInstances[0].emit("video:timeupdate");
    });

    // 默认收起，先展开再与输入框/发送按钮交互（否则它们在 aria-hidden 子树里不可达）
    fireEvent.click(screen.getByRole("button", { name: "展开 AI 对话" }));
    const input = await screen.findByPlaceholderText(aiPlaceholder);
    fireEvent.change(input, { target: { value: "这里讲了什么？" } });
    fireEvent.click(screen.getByRole("button", { name: /发送/ }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/chat/stream",
        expect.objectContaining({ method: "POST" }),
      );
    });
    const chatCall = fetchMock.mock.calls.find(([input]) => String(input).endsWith("/api/chat/stream"));
    expect(chatCall).toBeDefined();
    const payload = JSON.parse(String(chatCall?.[1]?.body));
    expect(payload).toMatchObject({
      course_id: "course-1",
      selected_subtitle: "",
      // A3：无引用时 start/end 是 Anchor 区间（null），播放位置走 current_time
      start_time: null,
      end_time: null,
      current_time: 75.5,
      video_duration: 600,
      user_question: "这里讲了什么？",
    });
  });

  it("CC 开关注入播放器控制栏（name=cc / position=right / index=25），否决浮层", async () => {
    mockMediaFetch({
      subtitle: "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n字幕\n",
    });
    renderPlayer();
    await waitFor(() => expect(artInstances).toHaveLength(1));
    const cc = artInstances[0].controlOptions.find((o: any) => o.name === "cc");
    // 锁死「不许退回右上角浮层」：必须落在控制栏、紧贴进度条行
    expect(cc).toBeDefined();
    expect(cc.position).toBe("right");
    expect(cc.index).toBe(25);
  });

  it("点击 CC 控件可开关字幕显示", async () => {
    mockMediaFetch({
      subtitle: "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n字幕\n",
    });
    const { container } = renderPlayer();
    await waitFor(() => expect(artInstances).toHaveLength(1));
    const cc = artInstances[0].controlOptions.find((o: any) => o.name === "cc");

    act(() => {
      artInstances[0].currentTime = 2;
      artInstances[0].emit("video:timeupdate");
    });
    expect(await screen.findByText("字幕")).toBeInTheDocument();

    act(() => cc.click());
    await waitFor(() => expect(container.querySelector(".subtitle-overlay")).toBeNull());

    act(() => cc.click());
    expect(await screen.findByText("字幕")).toBeInTheDocument();
  });

  it("无字幕时 CC 控件点击被守卫拦截，不报错", async () => {
    mockMediaFetch(); // 无字幕
    const { container } = renderPlayer();
    await waitFor(() => expect(artInstances).toHaveLength(1));
    const cc = artInstances[0].controlOptions.find((o: any) => o.name === "cc");
    expect(cc).toBeDefined();
    // 无字幕：点击不应切换、不应抛错（守卫拦截）
    expect(() => act(() => cc.click())).not.toThrow();
    expect(container.querySelector(".subtitle-overlay")).toBeNull();
  });
});
