import { useCallback, useEffect, useRef, useState } from "react";
import {
  Button,
  Dropdown,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Progress,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  Upload,
  message,
} from "antd";
import { UploadOutlined } from "@ant-design/icons";
import { api } from "../../api/client";
import { EditCue, secToStr, validateCueAxis, suspiciousReason } from "../../utils/subtitleEdit";

interface MaterialRow {
  course_id: string;
  status: string;
  error_message?: string | null;
  courseware_format?: string | null;
  subtitle_status?: string;
  review_state?: string;
  course_type?: "theory" | "practice" | null;
  source_id?: number | null;
  source_filename?: string | null;
}

interface ColumnOption { id: number; filename: string; column_name: string; format: string; }

const FILE_TYPES = [
  { value: "video", label: "视频（mp4/webm）" },
  { value: "subtitle", label: "字幕（vtt/srt）" },
  { value: "courseware", label: "课件（md/pdf/pptx）" },
];

export default function Materials() {
  const [list, setList] = useState<MaterialRow[]>([]);
  const [columnsList, setColumnsList] = useState<ColumnOption[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadForm] = Form.useForm();
  const [fileList, setFileList] = useState<any[]>([]);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);

  // P4 字幕编辑器：编辑弹窗状态
  const [editOpen, setEditOpen] = useState(false);
  const [editRow, setEditRow] = useState<MaterialRow | null>(null);
  const [editCues, setEditCues] = useState<EditCue[]>([]);
  const [editRevision, setEditRevision] = useState("");
  const [editSaving, setEditSaving] = useState(false);
  const uploadFileType = Form.useWatch("file_type", uploadForm);

  const load = () => {
    setLoading(true);
    Promise.all([api.get("/materials"), api.get("/admin/project-context")])
      .then(([materials, context]) => {
        setList(materials.data);
        setColumnsList(context.data.sources.filter((item: ColumnOption) => item.format === "pptx"));
      })
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  // C：字幕生成后轮询进度（仅生成中的行每 3s 拉一次状态）。
  const [progressMap, setProgressMap] = useState<Record<string, number>>({});
  const listRef = useRef<MaterialRow[]>(list);
  listRef.current = list;
  const fetchMaterials = useCallback(() => api.get("/materials").then((r) => setList(r.data)), []);

  const generateSubtitle = async (course_id: string) => {
    try {
      await api.post(`/admin/materials/${encodeURIComponent(course_id)}/generate-subtitle`);
      message.success("已触发字幕生成");
      fetchMaterials();
    } catch (e: any) {
      message.error(e.response?.data?.detail || "触发生成失败");
    }
  };

  const cancelSubtitle = async (course_id: string) => {
    try {
      await api.post(`/admin/materials/${encodeURIComponent(course_id)}/cancel-subtitle`);
      message.success("已取消生成");
      fetchMaterials();
    } catch (e: any) {
      message.error(e.response?.data?.detail || "取消失败");
    }
  };

  // A4：标记字幕审核状态。生成完成(unreviewed)才可解锁自动证据；已审核可撤销。
  const toggleReview = async (row: MaterialRow) => {
    const next = row.review_state === "reviewed" ? "unreviewed" : "reviewed";
    try {
      await api.post(`/admin/materials/${encodeURIComponent(row.course_id)}/subtitle/review`, {
        review_state: next,
      });
      message.success(next === "reviewed" ? "已标记为已审核，解锁自动证据注入" : "已撤销审核");
      fetchMaterials();
    } catch (e: any) {
      message.error(e.response?.data?.detail || "操作失败");
    }
  };

  // P4 编辑器：打开弹窗并拉取当前 cues + revision 乐观锁
  const openEditor = async (row: MaterialRow) => {
    try {
      const res = await api.get(`/admin/materials/${encodeURIComponent(row.course_id)}/subtitle/cues`);
      const d = res.data;
      setEditRow(row);
      setEditCues(
        (d.cues || []).map((c: any) => ({
          start: Number(c.start) || 0,
          end: Number(c.end) || 0,
          text: c.text || "",
        })),
      );
      setEditRevision(d.revision);
      setEditOpen(true);
    } catch (e: any) {
      message.error(e.response?.data?.detail || "加载字幕失败");
    }
  };

  const changeCue = (i: number, field: keyof EditCue, value: any) => {
    setEditCues((prev) => prev.map((c, idx) => (idx === i ? { ...c, [field]: value } : c)));
  };

  const addCue = () => {
    setEditCues((prev) => [...prev, { start: 0, end: 0, text: "" }]);
  };

  const delCue = (i: number) => {
    setEditCues((prev) => prev.filter((_, idx) => idx !== i));
  };

  const moveCue = (i: number, dir: -1 | 1) => {
    setEditCues((prev) => {
      const j = i + dir;
      if (j < 0 || j >= prev.length) return prev;
      const next = [...prev];
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });
  };

  // P5 人工抽查：在播放器里定位到该 cue 起点（ArtPlayer 对照）
  const locateCue = (start: number) => {
    if (!editRow) return;
    window.open(`/player?course_id=${encodeURIComponent(editRow.course_id)}&t=${start}`, "_blank");
  };

  // P4 编辑器：保存（先前端校验时间轴，再 PUT；遇 409 冲突重新拉取）
  const saveCues = async () => {
    const issues = validateCueAxis(editCues);
    if (issues.length > 0) {
      message.error(`时间轴非法：第 ${issues[0].index + 1} 条 ${issues[0].reason}`);
      return;
    }
    setEditSaving(true);
    try {
      const res = await api.put(
        `/admin/materials/${encodeURIComponent(editRow!.course_id)}/subtitle/cues`,
        { cues: editCues, revision: editRevision },
      );
      setEditRevision(res.data.revision);
      if (editRow) setEditRow({ ...editRow, review_state: res.data.review_state });
      message.success("字幕已保存（编辑后需重新人工抽查）");
      fetchMaterials();
      setEditOpen(false);
    } catch (e: any) {
      if (e.response?.status === 409) {
        message.warning("字幕已被改动，已重新拉取最新内容，请确认后再保存");
        if (editRow) openEditor(editRow);
      } else {
        message.error(e.response?.data?.detail || "保存失败");
      }
    } finally {
      setEditSaving(false);
    }
  };

  const generating = list.some((r) => r.subtitle_status === "generating");
  useEffect(() => {
    if (!generating) return;
    let alive = true;
    const tick = async () => {
      const rows = listRef.current.filter((r) => r.subtitle_status === "generating");
      await Promise.all(
        rows.map(async (r) => {
          try {
            const res = await api.get(`/materials/${encodeURIComponent(r.course_id)}/subtitle-status`);
            const d = res.data;
            if (!alive) return;
            setProgressMap((m) => ({ ...m, [r.course_id]: Math.round((d.progress || 0) * 100) }));
            if (d.subtitle_status !== "generating" || d.error) fetchMaterials();
          } catch {
            /* 轮询失败不影响其他行 */
          }
        }),
      );
    };
    tick();
    const id = setInterval(tick, 3000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [generating, fetchMaterials]);

  // 上传文件（带进度条）
  const uploadFile = async (
    courseId: string,
    fileType: string,
    file: File,
    courseType: "theory" | "practice" = "theory",
    sourceId?: number,
  ) => {
    const fd = new FormData();
    fd.append("file", file);
    setUploading(true);
    setProgress(0);
    try {
      await api.post(
        `/admin/materials/upload?course_id=${encodeURIComponent(courseId)}&file_type=${fileType}&course_type=${courseType}${sourceId ? `&source_id=${sourceId}` : ""}`,
        fd,
        {
        headers: { "Content-Type": "multipart/form-data" },
        onUploadProgress: (e) => {
          if (e.total) setProgress(Math.round((e.loaded / e.total) * 100));
        },
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
    const { course_id, file_type, course_type, source_id } = await uploadForm.validateFields();
    const file = fileList[0]?.originFileObj || fileList[0];
    if (!file) {
      message.warning("请选择文件");
      return;
    }
    const ok = await uploadFile(course_id, file_type, file, course_type || "theory", source_id);
    if (ok) {
      setUploadOpen(false);
      setFileList([]);
      uploadForm.resetFields();
    }
  };

  const rescan = async (course_id: string) => {
    await api.post(`/admin/materials/${course_id}/rescan`);
    message.success("重新扫描完成");
    load();
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
    { title: "课程 ID", dataIndex: "course_id" },
    {
      title: "状态",
      dataIndex: "status",
      render: (v: string) => (v === "ready" ? <Tag color="green">ready</Tag> : <Tag color="red">error</Tag>),
    },
    {
      title: "字幕状态",
      dataIndex: "subtitle_status",
      render: (v: string, row: MaterialRow) => {
        const map: Record<string, string> = {
          ready: "已就绪",
          pending: "待生成",
          generating: "生成中",
          error: "失败",
        };
        return (
          <Space direction="vertical" size={2} style={{ display: "flex" }}>
            <span>{map[v] || v || "—"}</span>
            {row.subtitle_status === "generating" && (
              <Progress percent={progressMap[row.course_id] ?? 0} size="small" />
            )}
            {row.subtitle_status === "ready" && (
              <Tag color={row.review_state === "reviewed" ? "green" : "orange"}>
                {row.review_state === "reviewed" ? "已审核" : "未审核"}
              </Tag>
            )}
          </Space>
        );
      },
    },
    { title: "课件格式", dataIndex: "courseware_format", render: (v: string) => v || "—" },
    {
      title: "课程类型",
      dataIndex: "course_type",
      render: (value: string) => (
        <Tag color={value === "practice" ? "blue" : "default"}>
          {value === "practice" ? "实战/案例" : value === "theory" ? "理论/通用" : "未分类（旧数据）"}
        </Tag>
      ),
    },
    { title: "所属专栏", dataIndex: "source_filename", render: (value: string) => value || "待归类" },
    { title: "错误信息", dataIndex: "error_message", ellipsis: true, render: (v: string) => v || "—" },
    {
      title: "操作",
      render: (_: any, row: MaterialRow) => (
        <Space>
          <Button size="small" onClick={() => rescan(row.course_id)}>
            重新扫描
          </Button>
          {row.status === "ready" && row.subtitle_status === "generating" && (
            <Button size="small" danger onClick={() => cancelSubtitle(row.course_id)}>
              取消生成
            </Button>
          )}
          {row.status === "ready" && row.subtitle_status !== "generating" && (
            <Button size="small" onClick={() => generateSubtitle(row.course_id)}>
              生成字幕
            </Button>
          )}
          {row.subtitle_status === "ready" && (
            <>
              <Button
                size="small"
                type={row.review_state === "reviewed" ? "default" : "primary"}
                onClick={() => toggleReview(row)}
              >
                {row.review_state === "reviewed" ? "撤销审核" : "标记已审核"}
              </Button>
              <Button size="small" onClick={() => openEditor(row)}>
                编辑字幕
              </Button>
            </>
          )}
          <Dropdown
            menu={{
              items: FILE_TYPES.map((ft) => ({
                key: ft.value,
                label: `重新上传${ft.label}`,
                onClick: () => setReupload({ courseId: row.course_id, fileType: ft.value }),
              })),
            }}
          >
            <Button size="small">重新上传</Button>
          </Dropdown>
        </Space>
      ),
    },
  ];

  // P4/P5 编辑器表格列
  const editorColumns = [
    { title: "#", dataIndex: "idx", width: 48, render: (_: any, _r: any, i: number) => i + 1 },
    {
      title: "开始(s)",
      dataIndex: "start",
      width: 130,
      render: (_: any, _r: any, i: number) => (
        <Space direction="vertical" size={0}>
          <InputNumber
            min={0}
            step={0.1}
            value={editCues[i]?.start}
            onChange={(v) => changeCue(i, "start", v ?? 0)}
            style={{ width: 110 }}
          />
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>
            {secToStr(editCues[i]?.start || 0)}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "结束(s)",
      dataIndex: "end",
      width: 130,
      render: (_: any, _r: any, i: number) => (
        <Space direction="vertical" size={0}>
          <InputNumber
            min={0}
            step={0.1}
            value={editCues[i]?.end}
            onChange={(v) => changeCue(i, "end", v ?? 0)}
            style={{ width: 110 }}
          />
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>
            {secToStr(editCues[i]?.end || 0)}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "字幕文本",
      dataIndex: "text",
      render: (_: any, _r: any, i: number) => (
        <Input.TextArea
          autoSize={{ minRows: 1, maxRows: 3 }}
          value={editCues[i]?.text}
          onChange={(e) => changeCue(i, "text", e.target.value)}
        />
      ),
    },
    {
      title: "抽查",
      width: 96,
      render: (_: any, _r: any, i: number) => {
        const reason = suspiciousReason(editCues[i]);
        return reason ? <Tag color="warning">{reason}</Tag> : <Tag color="success">正常</Tag>;
      },
    },
    {
      title: "操作",
      width: 150,
      render: (_: any, _r: any, i: number) => (
        <Space size={0}>
          <Button size="small" onClick={() => locateCue(editCues[i]?.start || 0)}>
            定位
          </Button>
          <Button size="small" onClick={() => moveCue(i, -1)} disabled={i === 0}>
            ↑
          </Button>
          <Button size="small" onClick={() => moveCue(i, 1)} disabled={i === editCues.length - 1}>
            ↓
          </Button>
          <Popconfirm title="删除这条字幕？" onConfirm={() => delCue(i)}>
            <Button size="small" danger>
              删
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 16 }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          素材管理
        </Typography.Title>
        <Button type="primary" icon={<UploadOutlined />} onClick={() => setUploadOpen(true)}>
          上传文件
        </Button>
      </div>
      <Table rowKey="course_id" loading={loading} columns={columns} dataSource={list} pagination={false} />

      <Modal title="上传文件" open={uploadOpen} onOk={doUpload} onCancel={() => setUploadOpen(false)} okText="上传" confirmLoading={uploading}>
        <Form
          form={uploadForm}
          layout="vertical"
          initialValues={{ file_type: "video", course_type: "theory" }}
        >
          <Form.Item name="course_id" label="课程 ID" rules={[{ required: true }]}>
            <Input placeholder="如 course-001" />
          </Form.Item>
          <Form.Item name="file_type" label="文件类型" rules={[{ required: true }]}>
            <Select options={FILE_TYPES} />
          </Form.Item>
          {uploadFileType === "video" && (
            <>
              <Form.Item
                name="source_id"
                label="所属专栏"
                extra={columnsList.length ? "视频上传后会直接归入所选 PPT 专栏。" : "还没有 PPT 专栏，请先到“专栏管理 → 上传课件”。"}
                rules={[{ required: true, message: "请选择所属专栏" }]}
              >
                <Select
                  placeholder="选择 PPT 专栏"
                  options={columnsList.map((item) => ({ value: item.id, label: item.column_name || item.filename }))}
                />
              </Form.Item>
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
          <Form.Item label="文件" required>
            <Upload
              beforeUpload={() => false}
              fileList={fileList}
              onChange={({ fileList }) => setFileList(fileList.slice(-1))}
            >
              <Button icon={<UploadOutlined />}>选择文件</Button>
            </Upload>
          </Form.Item>
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

      {/* P4 编辑器 + P5 人工抽查：编辑 cue 表格、可疑高亮、定位对照、标记审核 */}
      <Modal
        title="字幕编辑 / 人工抽查"
        open={editOpen}
        onOk={saveCues}
        onCancel={() => setEditOpen(false)}
        okText="保存字幕"
        confirmLoading={editSaving}
        width={920}
        destroyOnClose
      >
        {editRow && (
          <Space style={{ marginBottom: 12 }}>
            <Typography.Text>课程 {editRow.course_id}</Typography.Text>
            <Typography.Text type="secondary">
              审核状态：
              <Tag color={editRow.review_state === "reviewed" ? "green" : "orange"}>
                {editRow.review_state === "reviewed" ? "已审核" : "未审核"}
              </Tag>
            </Typography.Text>
            <Button
              size="small"
              onClick={() => editRow && toggleReview(editRow)}
            >
              {editRow.review_state === "reviewed" ? "撤销审核" : "标记为已审核"}
            </Button>
          </Space>
        )}
        <Table
          rowKey={(_, i) => String(i)}
          size="small"
          pagination={false}
          columns={editorColumns}
          dataSource={editCues}
          scroll={{ y: 360 }}
        />
        <Button type="dashed" block style={{ marginTop: 12 }} onClick={addCue}>
          + 新增一条字幕
        </Button>
        <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0, fontSize: 12 }}>
          提示：保存后会自动复位为「未审核」，需重新人工抽查；黄色标记为超长（&gt;12s）/空文本等可疑项。
        </Typography.Paragraph>
      </Modal>
    </div>
  );
}
