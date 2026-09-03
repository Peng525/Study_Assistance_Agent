import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import LLMCallLogs from "../pages/admin/LLMCallLogs";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: { get: vi.fn(), delete: vi.fn() },
}));
vi.mock("antd", async (importOriginal) => {
  const actual = await importOriginal<any>();
  return {
    ...actual,
    Popconfirm: ({ children, onConfirm }: any) => <span onClick={onConfirm}>{children}</span>,
    message: { success: vi.fn(), error: vi.fn() },
  };
});

const summary = {
  id: 12,
  request_id: "request-12",
  user_id: 7,
  username: "student7",
  session_id: "session-7",
  course_id: "spring-ioc-005",
  video_name: "005.Spring - Ioc和DI.mp4",
  source_id: 1,
  start_time: 125,
  user_question: "IoC 为什么叫控制反转？",
  prompt_chars: 100,
  status: "success",
  attempted_models: ["qwen-plus"],
  final_model_name: "qwen-plus",
  fallback_count: 0,
  answer_chars: 20,
  error_category: null,
  error_code: null,
  error_message: null,
  created_at: "2026-09-03T10:00:00",
  completed_at: "2026-09-03T10:00:02",
};

beforeEach(() => {
  vi.clearAllMocks();
  (api.get as any).mockImplementation((url: string) => Promise.resolve({ data: url.endsWith("/12")
    ? {
      ...summary,
      request_messages: [
        { role: "system", content: "你是项目助教" },
        { role: "user", content: "IoC 为什么叫控制反转？" },
      ],
      answer_text: "因为控制权从业务代码转交给容器。",
    }
    : { items: [summary], total: 1, page: 1, page_size: 20 } }));
  (api.delete as any).mockResolvedValue({ data: { deleted_count: 1 } });
});

describe("AI 调用日志", () => {
  it("显示列表，并在详情中展示实际模型消息、回答和调用轨迹", async () => {
    render(<LLMCallLogs />);
    expect(await screen.findByText("IoC 为什么叫控制反转？")).toBeInTheDocument();
    expect(screen.getByText("7 · student7")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /详情/ }));
    expect(await screen.findByText("你是项目助教")).toBeInTheDocument();
    expect(screen.getByText("因为控制权从业务代码转交给容器。")).toBeInTheDocument();
    expect(screen.getAllByText("qwen-plus").length).toBeGreaterThan(0);
    expect(api.get).toHaveBeenCalledWith("/admin/llm-call-logs/12");
  });

  it("可按用户筛选并确认清空日志", async () => {
    render(<LLMCallLogs />);
    await screen.findByText("IoC 为什么叫控制反转？");
    fireEvent.change(screen.getByPlaceholderText("用户 ID"), { target: { value: "7" } });
    fireEvent.click(screen.getByRole("button", { name: /筛\s*选/ }));
    await waitFor(() => expect(api.get).toHaveBeenLastCalledWith(
      "/admin/llm-call-logs",
      expect.objectContaining({ params: expect.objectContaining({ user_id: 7 }) }),
    ));

    fireEvent.click(screen.getByRole("button", { name: /清空日志/ }));
    await waitFor(() => expect(api.delete).toHaveBeenCalledWith("/admin/llm-call-logs"));
  });
});
