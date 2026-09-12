import { useCallback, useEffect, useRef, useState } from "react";
import {
  Button,
  Dropdown,
  Form,
  Input,
  Modal,
  Progress,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  Upload,
  message,
} from "antd";
import {
  LoadingOutlined,
  MoreOutlined,
  PlayCircleOutlined,
  StopOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import { api } from "../../api/client";
import { adminMaterials, BatchResult } from "../../api/adminMaterials";
import SubtitleDrawer, { SubtitleDrawerRow } from "../../components/SubtitleDrawer";
import {
  type BatchMode,
  canSelect,
  deriveSubtitleState,
  formatElapsed,
  pickBatchIds,
} from "../../utils/subtitleStatus";

interface MaterialRow {
  course_id: string;
  status: string;
  error_message?: string | null;
  courseware_format?: string | null;
  subtitle_status?: string;
  subtitle_source?: string | null;
  subtitle_filename?: string | null;
  subtitle_relative_path?: string | null;
  subtitle_error?: string | null;
  subtitle_has_file?: boolean;
  subtitle_task_active?: boolean;
  subtitle_progress?: number;
  subtitle_slices_done?: number;
  subtitle_slices_total?: number;
  subtitle_phase?: string | null;
  subtitle_started_at?: number | null;
  subtitle_queue_position?: number;
  review_state?: string;
  course_type?: "theory" | "practice" | null;
  source_id?: number | null;
  source_filename?: string | null;
  series_id?: number | null;
  series_name?: string | null;
  scanned_at?: string | null;
  duration?: number | null;
}

interface MaterialsProps {
  seriesId: number;
  onConfigureKnowledge?: (courseId: string) => void;
}

const FILE_TYPES = [
  { value: "video", label: "视频（mp4/webm）" },
  { value: "subtitle", label: "字幕（vtt/srt）" },
  { value: "courseware", label: "课件（md/pdf/pptx）" },
];

/** 降级轨：拿不到切片总数时显示已运行时间（每秒自重渲）。 */
function ElapsedTimer({ since }: { since: number }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, []);
  const elapsed = Math.max(0, Math.floor(Date.now() / 1000 - since));
  return (
    <Typography.Text type="secondary" style={{ fontSize: 11 }}>
      已运行 {formatElapsed(elapsed)}
    </Typography.Text>
  );
}

export default function Materials({ seriesId, onConfigureKnowledge }: MaterialsProps) {
  const [list, setList] = useState<MaterialRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadForm] = Form.useForm();
  const [fileList, setFileList] = useState<any[]>([]);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);

  // v8：批量选择 + 字幕 Drawer 工作区
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [batchMode, setBatchMode] = useState<BatchMode>(null);
  const [batchBusy, setBatchBusy] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerRow, setDrawerRow] = useState<MaterialRow | null>(null);

  const uploadFileType = Form.useWatch("file_type", uploadForm);

  const load = () => {
    setLoading(true);
    api.get("/materials")
      .then((materials) => setList(materials.data))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  // C：字幕生成后轮询进度（仅当页面存在 generating 行时，每 3s 拉一次列表）
  //
  // ⚠️ 曾经的实现是"逐行调 subtitle-status 单查，把百分比存进 progressMap"，
  // 但 progressMap **只写不读** —— 渲染吃的是 list 里那行静态快照，
  // 而 list 只在任务终态才刷新，于是生成全程百分比定格在触发瞬间（AC-4 实质不通过）。
  // 现在直接整表刷新：列表端点的 _peek_runtime() 已经把内存 worker 的进度合并进来了，
  // 单查端点相对它没有增量信息（唯一多出的 task_error 与 DB 的 subtitle_error 重复），
  // 为一个零增量信息维护 N 倍请求 + 一套字段映射层是纯负债。
  const fetchMaterials = useCallback(
    () => adminMaterials.listMaterials().then((d) => setList(d)),
    [],
  );

  const generateSubtitle = async (course_id: string) => {
    try {
      await adminMaterials.generateSubtitle(course_id);
      message.success("已触发字幕生成");
      fetchMaterials();
    } catch (e: any) {
      message.error(e.response?.data?.detail || "触发生成失败");
    }
  };

  const cancelSubtitle = async (course_id: string) => {
    try {
      await adminMaterials.cancelSubtitle(course_id);
      message.success("已取消生成");
      fetchMaterials();
    } catch (e: any) {
      message.error(e.response?.data?.detail || "取消失败");
    }
  };

  // 人工校对只表达质量状态，不影响播放器展示或 AI Evidence 准入。
  const toggleReview = async (row: { course_id: string; review_state?: string }) => {
    const next = row.review_state === "reviewed" ? "unreviewed" : "reviewed";
    try {
      await adminMaterials.reviewSubtitle(row.course_id, next);
      message.success(next === "reviewed" ? "已标记为已校对" : "已撤销校对标记");
      fetchMaterials();
      setDrawerRow((prev) => (prev ? { ...prev, review_state: next } : prev));
    } catch (e: any) {
      message.error(e.response?.data?.detail || "操作失败");
    }
  };

  // ---- 批量操作（v8 §5.5A.5）----
  const scopedList = list.filter((row) => row.series_id === seriesId);

  const selectedRows = scopedList.filter(
    (r) => selectedKeys.includes(r.course_id) && canSelect(r, batchMode, null),
  );
  const { generateIds, cancelIds } = pickBatchIds(selectedRows);
  const globalBatchIds = pickBatchIds(scopedList);

  const startBatch = (mode: "generate" | "cancel") => {
    setSelectedKeys([]);
    setBatchMode(mode);
  };

  const exitBatch = () => {
    setSelectedKeys([]);
    setBatchMode(null);
  };

  const runBatch = async (
    label: string,
    fn: (ids: string[]) => Promise<BatchResult>,
    ids: string[],
  ) => {
    if (ids.length === 0) return;
    setBatchBusy(true);
    try {
      const r = await fn(ids);
      message.success(`${label}：成功 ${r.succeeded} / 失败 ${r.failed}`);
      const failed = r.results.filter((x) => !x.ok);
      if (failed.length > 0) {
        message.warning(
          `失败项：${failed
            .slice(0, 3)
            .map((x) => `${x.course_id}（${x.error}）`)
            .join("；")}${failed.length > 3 ? ` 等 ${failed.length} 项` : ""}`,
        );
        // 保留失败项和当前操作模式，管理员可以原地重试。
        setSelectedKeys(failed.map((item) => item.course_id));
      } else {
        exitBatch();
      }
      fetchMaterials();
    } catch (e: any) {
      message.error(e.response?.data?.detail || `${label}失败`);
    } finally {
      setBatchBusy(false);
    }
  };

  const generating = scopedList.some((r) => r.subtitle_status === "generating");
  const pollingRef = useRef(false);
  useEffect(() => {
    if (!generating) return;
    let alive = true;
    const tick = async () => {
      // 上一轮还没回来就跳过：慢请求堆积会让 3s 间隔形同虚设，且乱序回包会覆盖新数据
      if (pollingRef.current) return;
      pollingRef.current = true;
      try {
        const rows = await adminMaterials.listMaterials();
        if (alive) setList(rows);
      } catch {
        /* 单轮失败静默，下轮再试 —— 不弹 message.error 打断管理员 */
      } finally {
        pollingRef.current = false;
      }
    };
    tick();
    const id = setInterval(tick, 3000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [generating]);

  // 上传文件（带进度条）
  const uploadFile = async (
    courseId: string,
    fileType: string,
    file: File,
    courseType: "theory" | "practice" = "theory",
    sourceId?: number,
  ) => {
    setUploading(true);
    setProgress(0);
    try {
      await adminMaterials.upload(
        { courseId, fileType, file, courseType, sourceId, seriesId },
        (e) => {
          if (e.total) setProgress(Math.round((e.loaded / e.total) * 100));
        },
      );
      message.success("上传成功");
      load();
      return true;
    } catch (e: any) {
      message.error(e.response?.data?.detail || "上传失败");
      return false;
    } finally {
      setUploading(false);
      setProgress(0);
    }
  };

  const doUpload = async () => {
    const { course_id, file_type, course_type } = await uploadForm.validateFields();
    const file = fileList[0]?.originFileObj || fileList[0];
    if (!file) {
      message.warning("请选择文件");
      return;
    }
    const ok = await uploadFile(course_id, file_type, file, course_type || "theory");
    if (ok) {
      closeUpload();
    }
  };

  const closeUpload = () => {
    setUploadOpen(false);
    setFileList([]);
    setProgress(0);
    uploadForm.resetFields();
  };

  const selectUploadFile = (nextFileList: any[]) => {
    const next = nextFileList.slice(-1);
    setFileList(next);
    const filename = next[0]?.originFileObj?.name || next[0]?.name;
    if (!String(uploadForm.getFieldValue("course_id") || "").trim() && filename) {
      uploadForm.setFieldValue("course_id", filename.replace(/\.[^./\\]+$/, ""));
    }
  };

  // 重新上传（弹确认后覆盖）
  const [reupload, setReupload] = useState<{ courseId: string; fileType: string } | null>(null);
  const [reuploadFile, setReuploadFile] = useState<any[]>([]);

  const confirmReupload = async () => {
    if (!reupload) return;
    const file = reuploadFile[0]?.originFileObj || reuploadFile[0];
    if (!file) {
      message.warning("请选择文件");
      return;
    }
    const currentType = list.find((item) => item.course_id === reupload.courseId)?.course_type;
    const ok = await uploadFile(
      reupload.courseId,
      reupload.fileType,
      file,
      currentType || "theory",
    );
    if (ok) {
      setReupload(null);
      setReuploadFile([]);
    }
  };

  const columns = [
    {
      title: "课程标识",
      dataIndex: "course_id",
      width: 180,
      fixed: "left" as const,
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 80,
      render: (v: string) =>
        v === "ready" ? <Tag color="green">ready</Tag> : <Tag color="red">error</Tag>,
    },
    {
      // v8 §5.5A.3：这一列只回答"字幕现在什么状态"，不负责入口
      title: "字幕状态",
      dataIndex: "subtitle_status",
      width: 180,
      render: (_: any, row: MaterialRow) => {
        const st = deriveSubtitleState(row);

        // 正常轨：有切片总数 → 百分比 + 进度条 + 切片计数
        if (st.kind === "transcribing" && st.slicesTotal) {
          return (
            <Space direction="vertical" size={2} style={{ width: "100%" }}>
              <span style={{ fontSize: 12 }}>{st.label}</span>
              <Progress percent={st.percent} size="small" showInfo={false} />
              <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                {st.slicesDone} / {st.slicesTotal}
              </Typography.Text>
            </Space>
          );
        }
        // 降级轨：拿不到切片总数 → 显示已运行计时，绝不编造百分比
        if (st.kind === "transcribing") {
          return (
            <Space direction="vertical" size={2}>
              <span style={{ fontSize: 12 }}>{st.label}</span>
              {st.startedAt && <ElapsedTimer since={st.startedAt} />}
            </Space>
          );
        }
        const colorMap: Record<string, string> = {
          pending: "default",
          queued: "blue",
          merging: "cyan",
          unreviewed: "warning",
          reviewed: "success",
          error: "error",
        };
        const tag = (
          <Tag
            color={colorMap[st.kind]}
            icon={st.kind === "merging" ? <LoadingOutlined /> : undefined}
          >
            {st.label}
          </Tag>
        );
        // 失败详情走 Tooltip —— 不额外展开一行（v8 §5.5A.6）。
        return st.kind === "error" ? (
          <Tooltip title={row.subtitle_error || "无错误详情，请查看后端日志"}>{tag}</Tooltip>
        ) : (
          tag
        );
      },
    },
    {
      title: "课件",
      dataIndex: "courseware_format",
      width: 80,
      align: "center" as const,
      render: (v: string) => v || "—",
    },
    {
      title: "课程类型",
      dataIndex: "course_type",
      width: 80,
      render: (value: string) => (
        <Tag color={value === "practice" ? "blue" : "default"}>
          {value === "practice" ? "实战" : value === "theory" ? "理论" : "未分类"}
        </Tag>
      ),
    },
    {
      // v8 §5.5A.2：这一列只回答"有字幕吗、能进去看吗"。
      // 没有真实字幕就显示 —，绝不画灰色假图标（原则 P2）。
      title: "字幕",
      key: "subtitle",
      width: 110,
      align: "center" as const,
      render: (_: any, row: MaterialRow) => {
        if (row.subtitle_status === "ready" && row.subtitle_has_file) {
          return (
            <Button
              type="link"
              size="small"
              onClick={() => {
                setDrawerRow(row);
                setDrawerOpen(true);
              }}
            >
              查看字幕
            </Button>
          );
        }
        return <span style={{ color: "#ccc" }}>—</span>;
      },
    },
    {
      title: "更多",
      key: "more",
      width: 60,
      align: "center" as const,
      fixed: "right" as const,
      render: (_: any, row: MaterialRow) => (
        <Dropdown
          menu={{
            items: [{
              key: "knowledge", label: "配置课程知识",
              onClick: () => onConfigureKnowledge?.(row.course_id),
            }, {
              key: "reupload", label: "重新上传", icon: <UploadOutlined />,
              onClick: () => setReupload({ courseId: row.course_id, fileType: "video" }),
            }],
          }}
        >
          <Button type="text" size="small" icon={<MoreOutlined />} />
        </Dropdown>
      ),
    },
  ];

  return (
    <div>
      {/* 动作优先：默认先选动作，进入对应模式后才出现选择列。 */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          minHeight: 40,
          marginBottom: 12,
          padding: "4px 12px",
          borderRadius: 6,
          background: batchMode ? "#f0f5ff" : "transparent",
          transition: "background 0.2s",
        }}
      >
        {batchMode === null ? (
          <Space wrap>
            <Button
              type="primary"
              icon={<PlayCircleOutlined />}
              disabled={globalBatchIds.generateIds.length === 0}
              onClick={() => startBatch("generate")}
            >
              生成字幕
            </Button>
            <Button
              danger
              icon={<StopOutlined />}
              disabled={globalBatchIds.cancelIds.length === 0}
              onClick={() => startBatch("cancel")}
            >
              取消生成
            </Button>
          </Space>
        ) : (
          <Space wrap>
            <Typography.Text>
              {batchMode === "generate"
                ? "请选择要生成字幕的素材"
                : "请选择要取消生成的素材"}
            </Typography.Text>
            <Button onClick={exitBatch} disabled={batchBusy}>
              取消选择
            </Button>
            {batchMode === "generate" && (
              <Button
                type="primary"
                loading={batchBusy}
                disabled={generateIds.length === 0}
                onClick={() =>
                  runBatch("批量生成字幕", adminMaterials.batchGenerateSubtitle, generateIds)
                }
              >
                开始生成字幕（{generateIds.length}）
              </Button>
            )}
            {batchMode === "cancel" && (
              <Button
                danger
                loading={batchBusy}
                disabled={cancelIds.length === 0}
                onClick={() =>
                  runBatch("批量取消生成", adminMaterials.batchCancelSubtitle, cancelIds)
                }
              >
                取消生成（{cancelIds.length}）
              </Button>
            )}
          </Space>
        )}
        <Button type="primary" icon={<UploadOutlined />} onClick={() => setUploadOpen(true)}>
          上传视频
        </Button>
      </div>

      <Table
        rowKey="course_id"
        loading={loading}
        columns={columns}
        dataSource={scopedList}
        pagination={false}
        scroll={{ x: 900 }}
        rowSelection={batchMode ? {
          selectedRowKeys: selectedKeys,
          onChange: (keys) => setSelectedKeys(keys.map(String)),
          getCheckboxProps: (row) => ({ disabled: !canSelect(row, batchMode, null) }),
          preserveSelectedRowKeys: true,
          columnWidth: 48,
        } : undefined}
      />

      <Modal
        title="上传视频"
        open={uploadOpen}
        onOk={doUpload}
        onCancel={closeUpload}
        okText="上传"
        confirmLoading={uploading}
      >
        <Form
          form={uploadForm}
          layout="vertical"
          initialValues={{ file_type: "video", course_type: "theory" }}
        >
          <Form.Item label="文件" required>
            <Upload
              beforeUpload={() => false}
              fileList={fileList}
              onChange={({ fileList }) => selectUploadFile(fileList)}
            >
              <Button icon={<UploadOutlined />}>选择文件</Button>
            </Upload>
          </Form.Item>
          <Form.Item name="course_id" label="课程标识" rules={[{ required: true }]}>
            <Input placeholder="如 004.Spring - 容器和组件" />
          </Form.Item>
          <Form.Item name="file_type" hidden><Input /></Form.Item>
          {uploadFileType === "video" && (
            <>
              <Form.Item
                name="course_type"
                label="课程类型"
                extra="仅用于管理分类；理论和实战都会使用专栏总大纲与当前视频课件原文。"
                rules={[{ required: true }]}
              >
                <Select options={[{ value: "theory", label: "理论/通用" }, { value: "practice", label: "实战/案例" }]} />
              </Form.Item>
            </>
          )}
          {uploading && <Progress percent={progress} />}
        </Form>
      </Modal>

      {/* 重新上传（覆盖）确认弹窗 */}
      <Modal
        title="重新上传（将覆盖现有文件）"
        open={!!reupload}
        onOk={confirmReupload}
        onCancel={() => {
          setReupload(null);
          setReuploadFile([]);
        }}
        okText="上传覆盖"
        confirmLoading={uploading}
      >
        <p>
          课程 <strong>{reupload?.courseId}</strong> · 类型{" "}
          <strong>{FILE_TYPES.find((f) => f.value === reupload?.fileType)?.label}</strong>
          ，将覆盖现有文件。
        </p>
        <Upload
          beforeUpload={() => false}
          fileList={reuploadFile}
          onChange={({ fileList }) => setReuploadFile(fileList.slice(-1))}
        >
          <Button icon={<UploadOutlined />}>选择新文件</Button>
        </Upload>
        {uploading && <Progress percent={progress} />}
      </Modal>

      {/* 字幕工作区：查看 ⇄ 编辑在同一个 Drawer 内切换（不弹窗套弹窗） */}
      <SubtitleDrawer
        key={drawerRow?.course_id ?? "none"}
        open={drawerOpen}
        row={drawerRow as SubtitleDrawerRow | null}
        onClose={() => setDrawerOpen(false)}
        onSaved={fetchMaterials}
        onRegenerate={generateSubtitle}
        onReviewToggle={toggleReview}
      />
    </div>
  );
}
