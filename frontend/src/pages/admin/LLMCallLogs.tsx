import { useEffect, useState } from "react";
import {
  Button,
  Descriptions,
  Drawer,
  Empty,
  InputNumber,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { DeleteOutlined, EyeOutlined, ReloadOutlined } from "@ant-design/icons";
import { api } from "../../api/client";


interface LogRow {
  id: number;
  request_id: string;
  user_id: number;
  username: string;
  session_id?: string | null;
  course_id?: string | null;
  video_name?: string | null;
  source_id?: number | null;
  start_time?: number | null;
  user_question: string;
  prompt_chars: number;
  status: string;
  attempted_models: string[];
  final_model_name?: string | null;
  fallback_count: number;
  answer_chars: number;
  error_category?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  created_at?: string | null;
  completed_at?: string | null;
}

interface LogDetail extends LogRow {
  request_messages: Array<{ role?: string; content?: string }>;
  answer_text: string;
}

const statusLabels: Record<string, { text: string; color: string }> = {
  running: { text: "调用中", color: "processing" },
  success: { text: "成功", color: "green" },
  failed: { text: "模型失败", color: "red" },
  rejected: { text: "本地拒绝", color: "orange" },
  interrupted: { text: "流中断", color: "volcano" },
};

export default function LLMCallLogs() {
  const [rows, setRows] = useState<LogRow[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [userIdDraft, setUserIdDraft] = useState<number | null>(null);
  const [statusDraft, setStatusDraft] = useState<string | undefined>();
  const [filters, setFilters] = useState<{ userId?: number; status?: string }>({});
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<LogDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const response = await api.get("/admin/llm-call-logs", {
        params: {
          page,
          page_size: pageSize,
          user_id: filters.userId,
          status: filters.status,
        },
      });
      setRows(response.data.items);
      setTotal(response.data.total);
    } catch (error: any) {
      message.error(error.response?.data?.detail || "AI 调用日志加载失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, [page, pageSize, filters.userId, filters.status]);

  const applyFilters = () => {
    setPage(1);
    setFilters({ userId: userIdDraft || undefined, status: statusDraft });
  };

  const openDetail = async (id: number) => {
    setDetailLoading(true);
    try {
      const response = await api.get(`/admin/llm-call-logs/${id}`);
      setDetail(response.data);
    } catch (error: any) {
      message.error(error.response?.data?.detail || "日志详情加载失败");
    } finally {
      setDetailLoading(false);
    }
  };

  const clearAll = async () => {
    try {
      const response = await api.delete("/admin/llm-call-logs");
      message.success(`已清空 ${response.data.deleted_count} 条日志`);
      setDetail(null);
      setPage(1);
      await load();
    } catch (error: any) {
      message.error(error.response?.data?.detail || "清空日志失败");
    }
  };

  const columns = [
    { title: "日志 ID", dataIndex: "id", width: 80 },
    {
      title: "时间",
      dataIndex: "created_at",
      width: 170,
      render: (value: string | null) => (value ? new Date(value).toLocaleString() : "—"),
    },
    {
      title: "用户",
      width: 140,
      render: (_: unknown, row: LogRow) => `${row.user_id} · ${row.username}`,
    },
    {
      title: "课程/视频",
      width: 180,
      render: (_: unknown, row: LogRow) => row.video_name || row.course_id || "—",
    },
    {
      title: "提问",
      dataIndex: "user_question",
      ellipsis: true,
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 100,
      render: (value: string) => {
        const item = statusLabels[value] || { text: value, color: "default" };
        return <Tag color={item.color}>{item.text}</Tag>;
      },
    },
    {
      title: "最终模型",
      dataIndex: "final_model_name",
      width: 160,
      render: (value: string | null) => value || "—",
    },
    { title: "降级", dataIndex: "fallback_count", width: 70 },
    {
      title: "操作",
      width: 80,
      render: (_: unknown, row: LogRow) => (
        <Button size="small" icon={<EyeOutlined />} onClick={() => void openDetail(row.id)}>
          详情
        </Button>
      ),
    },
  ];

  return (
    <div>
      <Space style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}>
        <div>
          <Typography.Title level={4} style={{ marginBottom: 2 }}>AI 调用日志</Typography.Title>
          <Typography.Text type="secondary">
            仅管理员可见；保存实际模型输入、最终回答和调用轨迹，最多保留最新 500 条。
          </Typography.Text>
        </div>
        <Popconfirm title="确定清空全部 AI 调用日志？" onConfirm={() => void clearAll()}>
          <Button danger icon={<DeleteOutlined />}>清空日志</Button>
        </Popconfirm>
      </Space>

      <Space style={{ marginBottom: 16 }} wrap>
        <InputNumber
          min={1}
          placeholder="用户 ID"
          value={userIdDraft}
          onChange={(value) => setUserIdDraft(value)}
        />
        <Select
          allowClear
          placeholder="调用状态"
          style={{ width: 140 }}
          value={statusDraft}
          onChange={setStatusDraft}
          options={Object.entries(statusLabels).map(([value, item]) => ({
            value,
            label: item.text,
          }))}
        />
        <Button type="primary" onClick={applyFilters}>筛选</Button>
        <Button icon={<ReloadOutlined />} onClick={() => void load()}>刷新</Button>
      </Space>

      <Table
        rowKey="id"
        loading={loading}
        columns={columns}
        dataSource={rows}
        scroll={{ x: 1150 }}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          onChange: (nextPage, nextSize) => {
            setPage(nextSize !== pageSize ? 1 : nextPage);
            setPageSize(nextSize);
          },
        }}
      />

      <Drawer
        title={detail ? `AI 调用日志 #${detail.id}` : "AI 调用日志"}
        width={820}
        open={Boolean(detail)}
        loading={detailLoading}
        onClose={() => setDetail(null)}
      >
        {detail ? (
          <Space direction="vertical" size="large" style={{ width: "100%" }}>
            <Descriptions bordered size="small" column={2}>
              <Descriptions.Item label="用户">{detail.user_id} · {detail.username}</Descriptions.Item>
              <Descriptions.Item label="状态">{statusLabels[detail.status]?.text || detail.status}</Descriptions.Item>
              <Descriptions.Item label="课程">{detail.course_id || "—"}</Descriptions.Item>
              <Descriptions.Item label="视频">{detail.video_name || "—"}</Descriptions.Item>
              <Descriptions.Item label="专栏 Source ID">{detail.source_id ?? "—"}</Descriptions.Item>
              <Descriptions.Item label="播放位置">{formatTime(detail.start_time)}</Descriptions.Item>
              <Descriptions.Item label="Session ID" span={2}>{detail.session_id || "—"}</Descriptions.Item>
              <Descriptions.Item label="Request ID" span={2}>{detail.request_id}</Descriptions.Item>
            </Descriptions>

            <LogSection title="用户原始问题" content={detail.user_question} />

            <div>
              <Typography.Title level={5}>实际发送给模型的内容（{detail.prompt_chars} 字符）</Typography.Title>
              {detail.request_messages.length ? detail.request_messages.map((item, index) => (
                <div key={`${item.role || "message"}-${index}`} style={{ marginBottom: 12 }}>
                  <Tag>{item.role || "unknown"}</Tag>
                  <pre style={preStyle}>{item.content || ""}</pre>
                </div>
              )) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本请求未发送给模型" />}
            </div>

            <LogSection title={`最终回答（${detail.answer_chars} 字符）`} content={detail.answer_text || "—"} />

            <Descriptions bordered size="small" column={1} title="调用轨迹">
              <Descriptions.Item label="尝试模型">{detail.attempted_models.join(" → ") || "—"}</Descriptions.Item>
              <Descriptions.Item label="最终模型">{detail.final_model_name || "—"}</Descriptions.Item>
              <Descriptions.Item label="降级次数">{detail.fallback_count}</Descriptions.Item>
              <Descriptions.Item label="错误分类">{detail.error_category || "—"}</Descriptions.Item>
              <Descriptions.Item label="错误码">{detail.error_code || "—"}</Descriptions.Item>
              <Descriptions.Item label="安全错误信息">{detail.error_message || "—"}</Descriptions.Item>
            </Descriptions>
          </Space>
        ) : null}
      </Drawer>
    </div>
  );
}

const preStyle = {
  whiteSpace: "pre-wrap" as const,
  wordBreak: "break-word" as const,
  background: "var(--bg-panel)",
  border: "1px solid var(--border)",
  borderRadius: 6,
  padding: 12,
  maxHeight: 360,
  overflow: "auto",
};

function LogSection({ title, content }: { title: string; content: string }) {
  return (
    <div>
      <Typography.Title level={5}>{title}</Typography.Title>
      <pre style={preStyle}>{content}</pre>
    </div>
  );
}

function formatTime(value?: number | null) {
  if (value == null) return "—";
  const seconds = Math.max(0, Math.floor(value));
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}
