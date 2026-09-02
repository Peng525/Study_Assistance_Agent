import { useEffect, useRef, useState } from "react";
import { Button, Empty, Input, Space, Typography, message } from "antd";
import { ClearOutlined, SendOutlined } from "@ant-design/icons";
import { getToken } from "../store/auth";

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
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
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (prefill) setInput(prefill);
  }, [prefill]);

  useEffect(() => {
    listRef.current?.scrollTo?.({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const send = async () => {
    const question = input.trim();
    if (!question || streaming) return;
    setStreaming(true);
    setInput("");
    setMessages((m) => [...m, { role: "user", content: question }, { role: "assistant", content: "" }]);
    const anchorTime = selectedSubtitle && startTime != null ? startTime : currentTime;
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
          if (!line.startsWith("data:")) continue;
          const data = JSON.parse(line.slice(5).trim());
          if (data.session_id) setSessionId(data.session_id);
          if (data.done && data.model_name) setCurrentModel(data.model_name);
          if (data.attempt_reset) {
            setMessages((m) => {
              const copy = [...m];
              copy[copy.length - 1] = { ...copy[copy.length - 1], content: "" };
              return copy;
            });
          }
          if (data.fallback) {
            setHistoryNotice(data.notice || `正在切换到 ${data.to_model}`);
          }
          if (data.delta) {
            setMessages((m) => {
              const copy = [...m];
              copy[copy.length - 1] = {
                ...copy[copy.length - 1],
                content: copy[copy.length - 1].content + data.delta,
              };
              return copy;
            });
          }
          if (data.notice) setHistoryNotice(data.notice);
          if (data.error) {
            setMessages((m) => {
              const copy = [...m];
              copy[copy.length - 1] = { ...copy[copy.length - 1], content: data.error };
              return copy;
            });
          }
        }
      }
    } catch (e) {
      setMessages((m) => {
        const copy = [...m];
        copy[copy.length - 1] = { ...copy[copy.length - 1], content: "网络错误，请重试" };
        return copy;
      });
    } finally {
      setStreaming(false);
    }
  };

  const clearSession = async () => {
    if (!sessionId) {
      setMessages([]);
      return;
    }
    try {
      await fetch(`/api/chat/sessions/${sessionId}/clear`, {
        method: "POST",
        headers: { Authorization: `Bearer ${getToken()}` },
      });
      setMessages([]);
      setSessionId(null);
      setCurrentModel("");
      message.success("会话已清空");
    } catch {
      message.error("清空失败");
    }
  };

  return (
    <div
      style={{
        width: 390,
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
            {currentModel ? `当前模型：${currentModel}` : "已保留最近 5 轮历史"}
          </div>
        </div>
        <Button size="small" icon={<ClearOutlined />} onClick={clearSession}>
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
              style={{
                marginBottom: 12,
                textAlign: m.role === "user" ? "right" : "left",
              }}
            >
              <div
                style={{
                  display: "inline-block",
                  maxWidth: "85%",
                  padding: "8px 12px",
                  borderRadius: 8,
                  background: m.role === "user" ? "var(--primary)" : "var(--bg-panel)",
                  color: m.role === "user" ? "#fff" : "var(--text)",
                  whiteSpace: "pre-wrap",
                  textAlign: "left",
                }}
              >
                {m.content || (streaming && i === messages.length - 1 ? "思考中…" : "")}
              </div>
            </div>
          ))
        )}
        {historyNotice && (
          <div style={{ fontSize: 12, color: "var(--text-secondary)", textAlign: "center" }}>
            {historyNotice}
          </div>
        )}
        {messages.filter((m) => m.role === "user").length >= 5 && (
          <div
            style={{
              fontSize: 12,
              color: "#fa8c16",
              textAlign: "center",
              marginTop: 8,
            }}
          >
            已超过 5 轮，建议清空会话重新开始
          </div>
        )}
      </div>

      <div style={{ padding: 12, borderTop: "1px solid var(--border)" }}>
        <Space.Compact style={{ width: "100%" }}>
          <Input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onPressEnter={send}
            placeholder="例如：我现在看到在创建 subagent，我想知道创建 subagent 应该怎么做。"
            disabled={streaming}
          />
          <Button type="primary" icon={<SendOutlined />} onClick={send} loading={streaming}>
            发送
          </Button>
        </Space.Compact>
      </div>
    </div>
  );
}
