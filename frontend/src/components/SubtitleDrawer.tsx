import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Descriptions,
  Drawer,
  Empty,
  Input,
  InputNumber,
  Popconfirm,
  Space,
  Spin,
  Table,
  type TableProps,
  Tag,
  Typography,
  message,
} from "antd";
import {
  CheckCircleOutlined,
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import { adminMaterials } from "../api/adminMaterials";
import { EditCue, suspiciousReason, validateCueAxis } from "../utils/subtitleEdit";
import { isReviewed } from "../utils/subtitleStatus";

/**
 * 字幕工作区（PRD v8 §5.5A.6）。
 *
 * 为什么是 Drawer 而不是 Modal：字幕可能有几百~上千条 cue，760px 的 Modal
 * 只够看元数据和前 50 条预览。Drawer 占 65%~75% 宽度，是"当前页面里的第二工作区"，
 * 用户仍看得到左侧导航，不会像跳到新页面那样迷失位置。
 *
 * 为什么查看和编辑放同一个 Drawer：避免"查看 Modal → 点编辑 → 又弹一个编辑 Modal"
 * 的弹窗套弹窗（原则 P4）。两种模式在**同一层**切换。
 */

export interface SubtitleDrawerRow {
  course_id: string;
  subtitle_status?: string;
  review_state?: string;
  subtitle_source?: string | null;
  subtitle_filename?: string | null;
  subtitle_relative_path?: string | null;
  scanned_at?: string | null;
  duration?: number | null;
}

interface Props {
  open: boolean;
  row: SubtitleDrawerRow | null;
  onClose: () => void;
  /** 保存 / 审核后通知父级刷新列表 */
  onSaved: () => void;
  onRegenerate: (courseId: string) => void;
  onReviewToggle: (row: SubtitleDrawerRow) => void;
}

type Mode = "view" | "edit";

const fmtTime = (iso?: string | null) => (iso ? iso.replace("T", " ").slice(0, 16) : "—");

const fmtDuration = (sec?: number | null) => {
  if (sec === null || sec === undefined) return "—";
  const s = Math.floor(sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(r).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
};

/** 秒 → mm:ss / h:mm:ss（字幕时间轴显示） */
const ts = (sec: number) => {
  const s = Math.max(0, Math.floor(Number(sec) || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(r).padStart(2, "0");
  return h > 0 ? `${String(h).padStart(2, "0")}:${mm}:${ss}` : `${mm}:${ss}`;
};

const toCues = (d: any): EditCue[] =>
  (d?.cues || []).map((c: any) => ({
    start: Number(c.start) || 0,
    end: Number(c.end) || 0,
    text: c.text || "",
  }));

export default function SubtitleDrawer({
  open,
  row,
  onClose,
  onSaved,
  onRegenerate,
  onReviewToggle,
}: Props) {
  const [mode, setMode] = useState<Mode>("view");
  const [cues, setCues] = useState<EditCue[]>([]);
  const [revision, setRevision] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);

  const courseId = row?.course_id;
  const ready = row?.subtitle_status === "ready";

  // 打开即加载；关闭时重置，避免下次打开残留编辑态
  useEffect(() => {
    if (!open || !courseId || !ready) return;
    let alive = true;
    setLoading(true);
    adminMaterials
      .getCues(courseId)
      .then((d) => {
        if (!alive) return;
        setCues(toCues(d));
        setRevision(d?.revision || "");
      })
      .catch(() => {
        if (alive) {
          setCues([]);
          setRevision("");
        }
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [open, courseId, ready]);

  const reload = async () => {
    if (!courseId) return;
    const d = await adminMaterials.getCues(courseId);
    setCues(toCues(d));
    setRevision(d?.revision || "");
  };

  const changeCue = (i: number, field: keyof EditCue, value: any) =>
    setCues((prev) => prev.map((c, idx) => (idx === i ? { ...c, [field]: value } : c)));

  const delCue = (i: number) => setCues((prev) => prev.filter((_, idx) => idx !== i));
  const addCue = (afterIndex?: number) => {
    const insertAt = afterIndex === undefined ? cues.length : afterIndex + 1;
    const previous = cues[insertAt - 1];
    const next = cues[insertAt];
    const start = previous?.end ?? 0;
    if (next && next.start <= start) {
      message.warning("相邻字幕之间没有可插入的时间，请先调整时间轴");
      return;
    }
    const end = next ? Math.min(start + 2, next.start) : start + 2;
    const updated = [...cues];
    updated.splice(insertAt, 0, { start, end, text: "" });
    setCues(updated);
    requestAnimationFrame(() => {
      bodyRef.current
        ?.querySelector<HTMLTextAreaElement>(`[data-cue-index="${insertAt}"] textarea`)
        ?.focus();
    });
  };

  const save = async () => {
    if (!courseId) return;
    const issues = validateCueAxis(cues);
    if (issues.length > 0) {
      message.error(`时间轴非法：第 ${issues[0].index + 1} 条 ${issues[0].reason}`);
      return;
    }
    setSaving(true);
    try {
      const d = await adminMaterials.putCues(courseId, cues, revision);
      setRevision(d?.revision || "");
      message.success("已保存；字幕被修改，审核状态已重置为未审核");
      setMode("view");
      onSaved();
    } catch (e: any) {
      if (e?.response?.status === 409) {
        message.warning("字幕已被其他地方修改，已重新加载最新内容，请确认后再保存");
        await reload();
      } else {
        message.error(e?.response?.data?.detail || "保存失败");
      }
    } finally {
      setSaving(false);
    }
  };

  const reviewed = isReviewed(row || {});

  const viewColumns = [
    {
      title: "时间",
      width: 190,
      render: (_: any, c: EditCue) => (
        <Typography.Text style={{ fontVariantNumeric: "tabular-nums" }}>
          {ts(c.start)} - {ts(c.end)}
        </Typography.Text>
      ),
    },
    {
      title: "字幕内容",
      render: (_: any, c: EditCue) => {
        const reason = suspiciousReason(c);
        return (
          <Space size={4} wrap>
            {c.text ? (
              <span>{c.text}</span>
            ) : (
              <Typography.Text type="secondary">（空）</Typography.Text>
            )}
            {reason && <Tag color="warning">{reason}</Tag>}
          </Space>
        );
      },
    },
  ];

  const editColumns = [
    { title: "#", width: 48, render: (_: any, __: any, i: number) => i + 1 },
    {
      title: "时间开始",
      width: 130,
      render: (_: any, __: any, i: number) => (
        <InputNumber
          size="small"
          min={0}
          step={0.1}
          value={cues[i]?.start}
          onChange={(v) => changeCue(i, "start", v ?? 0)}
          style={{ width: 110 }}
        />
      ),
    },
    {
      title: "时间结束",
      width: 130,
      render: (_: any, __: any, i: number) => (
        <InputNumber
          size="small"
          min={0}
          step={0.1}
          value={cues[i]?.end}
          onChange={(v) => changeCue(i, "end", v ?? 0)}
          style={{ width: 110 }}
        />
      ),
    },
    {
      title: "字幕内容",
      render: (_: any, __: any, i: number) => (
        <div data-cue-index={i}>
          <Input.TextArea
            autoSize={{ minRows: 1, maxRows: 3 }}
            value={cues[i]?.text}
            onChange={(e) => changeCue(i, "text", e.target.value)}
          />
        </div>
      ),
    },
    {
      title: "",
      width: 92,
      render: (_: any, __: any, i: number) => (
        <Space size={4}>
          <Button
            size="small"
            title="在此条后插入"
            icon={<PlusOutlined />}
            onClick={() => addCue(i)}
          />
          <Popconfirm title="删除这条字幕？" onConfirm={() => delCue(i)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  // 给每行补一个稳定 key：antd 已弃用 rowKey 的 index 参数，且 cue 本身没有 id。
  // key 只在表格内部用，保存时回落到纯 EditCue，不会污染请求体。
  const dataSource = cues.map((c, i) => ({ ...c, key: i }));

  const tableProps: Pick<
    TableProps<EditCue>,
    "rowKey" | "size" | "pagination" | "scroll"
  > = {
    rowKey: "key",
    size: "small",
    // 上千条 cue 一次渲染会卡，超过 100 条才分页
    pagination: cues.length > 100 ? { pageSize: 100, size: "small" } : false,
    scroll: { y: "calc(100vh - 430px)" },
  };

  return (
    <Drawer
      title={row ? `字幕详情 · ${row.course_id}` : "字幕详情"}
      placement="right"
      width="70%"
      open={open}
      onClose={onClose}
      afterOpenChange={(o) => {
        if (!o) {
          setMode("view");
          setCues([]);
          setRevision("");
        }
      }}
    >
      {!row ? (
        <Empty description="未选择素材" />
      ) : (
        <div ref={bodyRef}>
          <Descriptions column={2} bordered size="small" style={{ marginBottom: 16 }}>
            <Descriptions.Item label="状态">
              {ready ? (
                <Tag color={reviewed ? "success" : "warning"}>
                  {reviewed ? "已审核" : "未审核"}
                </Tag>
              ) : (
                <Tag>{row.subtitle_status || "—"}</Tag>
              )}
            </Descriptions.Item>
            <Descriptions.Item label="来源">
              {row.subtitle_source === "whisper"
                ? "AI 自动生成 (Whisper)"
                : row.subtitle_source === "manual"
                  ? "人工上传"
                  : "—"}
            </Descriptions.Item>
            <Descriptions.Item label="字幕文件">{row.subtitle_filename || "—"}</Descriptions.Item>
            {/* 条数 = 解析后的 cue 数；一个 cue 可能占多行文本，所以不能用 VTT 行数代替 */}
            <Descriptions.Item label="字幕条数">
              {loading ? <Spin size="small" /> : `${cues.length} 条`}
            </Descriptions.Item>
            <Descriptions.Item label="时长">{fmtDuration(row.duration)}</Descriptions.Item>
            {/* scanned_at 是素材扫描时间，不是字幕生成完成时间 —— 措辞不能含糊 */}
            <Descriptions.Item label="最近扫描">{fmtTime(row.scanned_at)}</Descriptions.Item>
            {/* 后端脱敏后的项目相对路径，前端原样显示，不自行拼接 */}
            <Descriptions.Item label="存储位置" span={2}>
              {row.subtitle_relative_path ? (
                <Typography.Text code copyable={{ text: row.subtitle_relative_path }}>
                  {row.subtitle_relative_path}
                </Typography.Text>
              ) : (
                "—"
              )}
            </Descriptions.Item>
          </Descriptions>

          {mode === "view" ? (
            <Space wrap style={{ marginBottom: 16 }}>
              {reviewed ? (
                <Popconfirm
                  title="撤销审核后，该字幕将不再自动用于问答，确定撤销？"
                  onConfirm={() => onReviewToggle(row)}
                >
                  <Button icon={<CheckCircleOutlined />}>撤销审核</Button>
                </Popconfirm>
              ) : (
                <Button icon={<CheckCircleOutlined />} onClick={() => onReviewToggle(row)}>
                  标记已审核
                </Button>
              )}
              <Button icon={<ReloadOutlined />} onClick={() => onRegenerate(row.course_id)}>
                重新生成
              </Button>
              <Button
                type="primary"
                icon={<EditOutlined />}
                disabled={!ready}
                onClick={() => setMode("edit")}
              >
                编辑字幕
              </Button>
            </Space>
          ) : (
            <Space wrap style={{ marginBottom: 16 }}>
              <Button onClick={() => setMode("view")}>取消编辑</Button>
              <Button type="primary" loading={saving} onClick={save}>
                保存修改
              </Button>
            </Space>
          )}

          {!ready ? (
            <Alert
              type="info"
              showIcon
              message={`当前字幕状态：${row.subtitle_status || "未知"}，暂无字幕内容可查看`}
              description="可点右上角「重新生成」重新触发 Whisper 转写。"
            />
          ) : loading ? (
            <Spin />
          ) : mode === "view" ? (
            <>
              <Table
                {...tableProps}
                columns={viewColumns}
                dataSource={dataSource}
                locale={{ emptyText: "暂无字幕内容" }}
              />
              <Typography.Paragraph
                type="secondary"
                style={{ marginTop: 12, marginBottom: 0, fontSize: 12 }}
              >
                黄色标记为可疑项（超长 &gt;12s / 空文本），人工抽查时可重点核对。
              </Typography.Paragraph>
            </>
          ) : (
            <>
              <Table
                {...tableProps}
                columns={editColumns}
                dataSource={dataSource}
                locale={{ emptyText: "暂无字幕内容" }}
              />
              <Button
                type="dashed"
                block
                style={{ marginTop: 12 }}
                icon={<PlusOutlined />}
                onClick={() => addCue()}
              >
                {cues.length ? "在末尾新增一条字幕" : "新增第一条字幕"}
              </Button>
              <Typography.Paragraph
                type="secondary"
                style={{ marginTop: 8, marginBottom: 0, fontSize: 12 }}
              >
                保存后会自动复位为「未审核」，需重新人工抽查。
              </Typography.Paragraph>
            </>
          )}
        </div>
      )}
    </Drawer>
  );
}
