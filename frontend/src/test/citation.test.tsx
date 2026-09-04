import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import AISidebar from "../components/AISidebar";
import { Citation } from "../components/CitationCard";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const historyResponse = (messages: unknown[] = []) => ({
  ok: true,
  status: 200,
  json: async () => ({ session_id: "s", column: null, messages }),
});

function makeStreamFetch(capture: Array<Record<string, unknown>>) {
  const fetchMock = vi.fn().mockImplementation((input: string, init?: RequestInit) => {
    if (String(input).includes("column-session")) return Promise.resolve(historyResponse());
    if (String(input).includes("/api/chat/stream")) {
      if (init?.body) capture.push(JSON.parse(String(init.body)));
      const bytes = new TextEncoder().encode(
        'data: {"delta":"ok"}\n\ndata: {"done":true,"model_name":"qwen-plus"}\n\n',
      );
      let read = false;
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
    }
    return Promise.resolve(historyResponse());
  });
  return fetchMock;
}

describe("Active Citation 连续追问", () => {
  it("连续追问第二轮仍携带同一引用与真实时间区间（核心回归）", async () => {
    const payloads: Array<Record<string, unknown>> = [];
    vi.stubGlobal("fetch", makeStreamFetch(payloads));

    const citation: Citation = { text: "这是被选中的字幕", start: 12, end: 15 };
    render(<AISidebar courseId="c1" citation={citation} currentTime={30} />);
    const textbox = await screen.findByRole("textbox");
    await waitFor(() => expect(textbox).not.toBeDisabled());

    fireEvent.change(textbox, { target: { value: "第一问" } });
    fireEvent.click(screen.getByRole("button", { name: /发\s*送/ }));
    await waitFor(() => expect(screen.getByText("ok")).toBeInTheDocument());

    fireEvent.change(textbox, { target: { value: "第二问" } });
    fireEvent.click(screen.getByRole("button", { name: /发\s*送/ }));
    await waitFor(() => expect(payloads).toHaveLength(2));

    const [p1, p2] = payloads;
    // 两轮都必须带同一 selected_subtitle —— 这是「连续追问第二轮丢失引用」的回归点
    expect(p1.selected_subtitle).toBe("这是被选中的字幕");
    expect(p2.selected_subtitle).toBe("这是被选中的字幕");
    // 引用区间用 cue 真实 start/end；current_time 是播放位置（时间窗基准）
    expect(p1).toMatchObject({ start_time: 12, end_time: 15, current_time: 30 });
    expect(p2).toMatchObject({ start_time: 12, end_time: 15, current_time: 30 });
  });

  it("无引用时只携带播放位置，不发送字幕文本", async () => {
    const payloads: Array<Record<string, unknown>> = [];
    vi.stubGlobal("fetch", makeStreamFetch(payloads));

    render(<AISidebar courseId="c1" currentTime={88.5} videoDuration={600} />);
    const textbox = await screen.findByRole("textbox");
    await waitFor(() => expect(textbox).not.toBeDisabled());

    fireEvent.change(textbox, { target: { value: "随便问问" } });
    fireEvent.click(screen.getByRole("button", { name: /发\s*送/ }));
    await waitFor(() => expect(payloads).toHaveLength(1));

    expect(payloads[0]).toMatchObject({
      selected_subtitle: "",
      start_time: null,
      end_time: null,
      current_time: 88.5,
      video_duration: 600,
    });
  });

  it("清空会话会一并调用 onClearCitation 清掉引用", async () => {
    const onClear = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((input: string) =>
        String(input).includes("column-session")
          ? Promise.resolve(historyResponse())
          : Promise.resolve({ ok: true, status: 200, json: async () => ({}) }),
      ),
    );
    const citation: Citation = { text: "引用内容", start: 1, end: 2 };
    render(<AISidebar courseId="c1" citation={citation} onClearCitation={onClear} />);

    // 引用卡片渲染
    expect(await screen.findByTestId("citation-card")).toBeInTheDocument();
    // 清空会话应触发 onClearCitation（引用锚点随会话一起清掉）
    fireEvent.click(screen.getByRole("button", { name: /清空会话/ }));
    await waitFor(() => expect(onClear).toHaveBeenCalled());
  });
});
