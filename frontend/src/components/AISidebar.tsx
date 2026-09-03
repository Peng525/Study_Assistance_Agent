import { useEffect, useRef, useState } from "react";
import { Button, Empty, Input, Typography, message } from "antd";
import { ClearOutlined, SendOutlined } from "@ant-design/icons";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { getToken } from "../store/auth";

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  course_id?: string | null;
  video_name?: string | null;
  start_time?: number | null;
  model_name?: string | null;
  thinking_ms?: number | null;
  created_at?: string | null;
}

interface AISidebarProps {
  courseId: string;
  prefill?: string; // 选中字幕预填模板
  selectedSubtitle?: string;
  startTime?: number | null;
  endTime?: number | null;
  currentTime?: number;
  videoDuration?: number | null;
  onContextConsumed?: () => void;
}

export default function AISidebar({
  courseId,
  prefill,
  selectedSubtitle = "",
  startTime,
  endTime,
  currentTime = 0,
  videoDuration,
  onContextConsumed,
}: AISidebarProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState(prefill || "");
  const [streaming, setStreaming] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [historyNotice, setHistoryNotice] = useState("");
  const [currentModel, setCurrentModel] = useState("");
  const [columnName, setColumnName] = useState("");
  const [currentVideoName, setCurrentVideoName] = useState(courseId);
  const [historyLoading, setHistoryLoading] = useState(true);
  const listRef = useRef<HTMLDivElement>(null);
  const requestInFlightRef = useRef(false);
  const viewIdRef = useRef(0);

  useEffect(() => {
    if (prefill) setInput(prefill);
  }, [prefill]);

  useEffect(() => {
    const controller = new AbortController();
    const viewId = ++viewIdRef.current;
    requestInFlightRef.current = false;
    setStreaming(false);
    setHistoryLoading(true);
    setMessages([]);
    setSessionId(null);
    setColumnName("");
    setCurrentVideoName(courseId);
    setCurrentModel("");
    const loadHistory = async () => {
      setHistoryNotice("");
      try {
        const resp = await fetch(
          `/api/chat/column-session?course_id=${encodeURIComponent(courseId)}`,
          {
            headers: { Authorization: `Bearer ${getToken()}` },
            signal: controller.signal,
          },
        );
        if (viewId !== viewIdRef.current) return;
        if (resp.status === 404) {
          return;
        }
        if (!resp.ok) throw new Error("load failed");
        const data = await resp.json();
        const restored = (data.messages || []) as ChatMessage[];
        if (!requestInFlightRef.current) setMessages(restored);
        setSessionId(data.session_id);
        setColumnName(data.column?.name || "");
        setCurrentVideoName(data.column?.current_video_name || courseId);
        const lastAssistant = [...restored].reverse().find((item) => item.role === "assistant");
        setCurrentModel(lastAssistant?.model_name || "");
      } catch (error) {
        if ((error as Error).name !== "AbortError" && viewId === viewIdRef.current) {
          message.error("完整对话加载失败，请刷新重试");
        }
      } finally {
        if (viewId === viewIdRef.current) setHistoryLoading(false);
      }
    };
    loadHistory();
    return () => controller.abort();
  }, [courseId]);

  useEffect(() => {
    listRef.current?.scrollTo?.({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const send = async () => {
    const question = input.trim();
    if (!question || streaming || historyLoading) return;
    const sendViewId = viewIdRef.current;
    requestInFlightRef.current = true;
    setStreaming(true);
    setInput("");
    const anchorTime = selectedSubtitle && startTime != null ? startTime : currentTime;
    setMessages((m) => [
      ...m,
      {
        role: "user",
        content: question,
        course_id: courseId,
        video_name: currentVideoName,
        start_time: anchorTime,
      },
      { role: "assistant", content: "" },
    ]);
    onContextConsumed?.();

    try {
      const resp = await fetch("/api/chat/stream", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${getToken()}`,
        },
        body: JSON.stringify({
          course_id: courseId,
          selected_subtitle: selectedSubtitle,
          start_time: anchorTime,
          end_time: selectedSubtitle ? endTime ?? anchorTime : anchorTime,
          video_duration: videoDuration,
          user_question: question,
          session_id: sessionId,
        }),
      });

      if (!resp.ok) throw new Error("request failed");

      const reader = resp.body?.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (reader) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n\n");
        buffer = lines.pop() || "";
        for (const line of lines) {
          if (sendViewId !== viewIdRef.current) return;
          if (!line.startsWith("data:")) continue;
          const data = JSON.parse(line.slice(5).trim());
          if (data.session_id) setSessionId(data.session_id);
          if (data.done && data.model_name) {
            setCurrentModel(data.model_name);
            if (data.thinking_ms != null) {
              setMessages((m) => {
                const copy = [...m];
                const last = copy[copy.length - 1];
                if (last?.role === "assistant") {
                  copy[copy.length - 1] = { ...last, thinking_ms: data.thinking_ms };
                }
                return copy;
              });
            }
          }
          if (data.attempt_reset) {
            setMessages((m) => {
              const copy = [...m];
              const last = copy[copy.length - 1];
              if (!last || last.role !== "assistant") return copy;
              copy[copy.length - 1] = { ...last, content: "", thinking_ms: null };
              return copy;
            });
          }
          if (data.fallback) {
            setHistoryNotice(data.notice || `正在切换到 ${data.to_model}`);
          }
          if (data.delta) {
            setMessages((m) => {
              const copy = [...m];
              const last = copy[copy.length - 1];
              if (!last || last.role !== "assistant") {
                return [...copy, {
                  role: "assistant",
                  content: data.delta,
                  thinking_ms: data.thinking_ms,
                }];
              }
              copy[copy.length - 1] = {
                ...last,
                content: last.content + data.delta,
                thinking_ms: data.thinking_ms ?? last.thinking_ms,
              };
              return copy;
            });
          }
          if (data.notice) setHistoryNotice(data.notice);
          if (data.error) {
            setMessages((m) => {
              const copy = [...m];
              const last = copy[copy.length - 1];
              if (!last || last.role !== "assistant") {
                return [...copy, { role: "assistant", content: data.error }];
              }
              copy[copy.length - 1] = { ...last, content: data.error };
              return copy;
            });
          }
        }
      }
    } catch (e) {
      if (sendViewId !== viewIdRef.current) return;
      setMessages((m) => {
        const copy = [...m];
        const last = copy[copy.length - 1];
        if (!last || last.role !== "assistant") {
          return [...copy, { role: "assistant", content: "网络错误，请重试" }];
        }
        copy[copy.length - 1] = { ...last, content: "网络错误，请重试" };
        return copy;
      });
    } finally {
      requestInFlightRef.current = false;
      if (sendViewId === viewIdRef.current) setStreaming(false);
    }
  };

  const clearSession = async () => {
    if (!sessionId) {
      setMessages([]);
      return;
    }
    try {
      const resp = await fetch(`/api/chat/sessions/${sessionId}/clear`, {
        method: "POST",
        headers: { Authorization: `Bearer ${getToken()}` },
      });
      if (!resp.ok) throw new Error("clear failed");
      setMessages([]);
      setCurrentModel("");
      message.success("会话已清空");
    } catch {
      message.error("清空失败");
    }
  };

  return (
    <div
      style={{
        width: "100%",
        display: "flex",
        flexDirection: "column",
        height: "100%",
        background: "var(--bg-sidebar)",
        borderLeft: "1px solid var(--border)",
      }}
    >
      <div
        style={{
          padding: "12px 16px",
          borderBottom: "1px solid var(--border)",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <div>
          <Typography.Text strong>AI 学习搭档</Typography.Text>
          <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>
            {columnName ? `专栏：${columnName} · 完整对话已保存` : "当前视频使用独立会话"}
          </div>
          {currentModel && (
            <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>
              当前模型：{currentModel}
            </div>
          )}
        </div>
        <Button size="small" icon={<ClearOutlined />} onClick={clearSession} disabled={historyLoading}>
          清空会话
        </Button>
      </div>

      <div
        ref={listRef} style={{ flex: 1, overflowY: "auto", padding: 16 }}
      >
        {messages.length === 0 ? (
          <Empty description="选中字幕右键提问，或直接输入问题" />
        ) : (
          messages.map((m, i) => (
            <div
              key={i}
              className={`ai-message-row ai-message-row--${m.role}`}
            >
              {m.role === "user" && (m.video_name || m.start_time != null) && (
                <div
                  style={{
                    fontSize: 11,
                    color: "var(--text-secondary)",
                    marginBottom: 4,
                  }}
                >
                  {m.video_name || m.course_id || "当前视频"}
                  {m.start_time != null ? ` · ${formatTime(m.start_time)}` : ""}
                </div>
              )}
              {m.role === "assistant" && m.thinking_ms != null && (
                <div
                  className="ai-thinking-time"
                  title="从请求进入后端到最终有效回答首字返回，包含网络、排队和模型处理时间"
                >
                  思考耗时 {(m.thinking_ms / 1000).toFixed(1)} 秒
                </div>
              )}
              <div
                className={m.role === "assistant" ? "ai-answer" : "ai-user-message"}
              >
                {m.role === "assistant" && m.content ? (
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
                ) : (
                  m.content || (streaming && i === messages.length - 1 ? "思考中…" : "")
                )}
              </div>
            </div>
          ))
        )}
        {historyNotice && (
          <div style={{ fontSize: 12, color: "var(--text-secondary)", textAlign: "center" }}>
            {historyNotice}
          </div>
        )}
      </div>

      <div className="ai-composer">
        <div className="ai-composer__row">
          <Input.TextArea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(event) => {
              if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
              event.preventDefault();
              void send();
            }}
            autoSize={{ minRows: 2, maxRows: 5 }}
            placeholder="例如：我现在看到在创建 subagent，我想知道创建 subagent 应该怎么做。"
            disabled={streaming || historyLoading}
          />
          <Button
            type="primary"
            size="large"
            icon={<SendOutlined />}
            onClick={send}
            loading={streaming}
            disabled={historyLoading}
          >
            发送
          </Button>
        </div>
      </div>
    </div>
  );
}

function formatTime(seconds: number) {
  const safe = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(safe / 60);
  const remainder = safe % 60;
  return `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}
