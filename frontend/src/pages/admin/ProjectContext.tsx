import { useEffect, useState } from "react";
import { Alert, Button, Card, Drawer, Dropdown, Empty, Input, Modal, Select, Space, Spin, Table, Tabs, Tag, Typography, Upload, message } from "antd";
import { ArrowLeftOutlined, DeleteOutlined, EditOutlined, MoreOutlined, PlusOutlined, UploadOutlined } from "@ant-design/icons";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../../api/client";
import { deriveSubtitleState } from "../../utils/subtitleStatus";
import Materials from "./Materials";

interface Source {
  id: number;
  filename: string;
  page_count: number;
  outline_text: string;
  outline_status: string;
  outline_updated_at?: string | null;
}

interface Series {
  id: number;
  name: string;
  context_epoch: number;
  video_count: number;
  source: Source | null;
}

interface UnassignedVideo {
  course_id: string;
  status: string;
  subtitle_status?: string;
  review_state?: string;
  series_id?: number | null;
}

const outlineLabels: Record<string, { label: string; color?: string }> = {
  empty: { label: "未生成" },
  draft: { label: "待审核", color: "orange" },
  ready: { label: "已启用", color: "green" },
  stale: { label: "已失效", color: "red" },
  error: { label: "生成失败", color: "red" },
};

const subtitleColors: Record<string, string> = {
  pending: "default",
  queued: "blue",
  transcribing: "blue",
  merging: "cyan",
  unreviewed: "orange",
  reviewed: "green",
  error: "red",
};

export default function ProjectContext() {
  const navigate = useNavigate();
  const { seriesId } = useParams();
  const id = seriesId ? Number(seriesId) : null;
  const [rows, setRows] = useState<Series[]>([]);
  const [unassigned, setUnassigned] = useState<UnassignedVideo[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [nameEditor, setNameEditor] = useState<{ id?: number; name: string } | null>(null);
  const [assigning, setAssigning] = useState<UnassignedVideo | null>(null);
  const [assignSeriesId, setAssignSeriesId] = useState<number>();
  const [outlineOpen, setOutlineOpen] = useState(false);
  const [outlineEditing, setOutlineEditing] = useState(false);
  const [outlineText, setOutlineText] = useState("");

  const load = async () => {
    setLoading(true);
    try {
      const [columns, materials] = await Promise.all([
        api.get<Series[]>("/admin/columns"),
        api.get<UnassignedVideo[]>("/materials"),
      ]);
      setRows(columns.data);
      setUnassigned(materials.data.filter((item) => item.series_id == null));
    } catch (error: any) {
      message.error(error.response?.data?.detail || "专栏加载失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);
  const current = rows.find((row) => row.id === id);

  const saveName = async () => {
    if (!nameEditor?.name.trim()) return;
    setBusy(true);
    try {
      if (nameEditor.id) await api.put(`/admin/columns/${nameEditor.id}`, { name: nameEditor.name });
      else await api.post("/admin/columns", { name: nameEditor.name });
      message.success(nameEditor.id ? "专栏已重命名" : "专栏已创建");
      setNameEditor(null);
      await load();
    } catch (error: any) {
      message.error(error.response?.data?.detail || "保存失败");
    } finally { setBusy(false); }
  };

  const deleteSeries = (row: Series) => Modal.confirm({
    title: `删除专栏“${row.name}”？`,
    content: "只有没有课件、视频和真实会话内容的空专栏可以删除。",
    okText: "删除",
    okButtonProps: { danger: true },
    onOk: async () => {
      try {
        await api.delete(`/admin/columns/${row.id}`);
        message.success("专栏已删除");
        await load();
      } catch (error: any) {
        message.error(error.response?.data?.detail || "删除失败");
      }
    },
  });

  const assignVideo = async () => {
    if (!assigning || !assignSeriesId) return;
    setBusy(true);
    try {
      await api.put(`/admin/project-context/videos/${encodeURIComponent(assigning.course_id)}/series`, { series_id: assignSeriesId });
      message.success("已归入专栏");
      setAssigning(null);
      setAssignSeriesId(undefined);
      await load();
    } catch (error: any) {
      message.error(error.response?.data?.detail || "归类失败");
    } finally { setBusy(false); }
  };

  const uploadPpt = (file: File) => {
    if (!current) return Upload.LIST_IGNORE;
    Modal.confirm({
      title: current.source ? "更换当前课件？" : "上传专栏课件？",
      content: current.source
        ? "内容变化会切换上下文世代，并使总大纲和课程文本失效；视频与字幕保持不变。"
        : "课件将成为该专栏的当前 PPT。",
      onOk: async () => {
        const form = new FormData();
        form.append("file", file);
        form.append("series_id", String(current.id));
        setBusy(true);
        try {
          const config = { headers: { "Content-Type": "multipart/form-data" } };
          const response = current.source
            ? await api.put(`/admin/project-context/sources/${current.source.id}`, form, config)
            : await api.post("/admin/project-context/sources", form, config);
          message[response.data.unchanged ? "info" : "success"](
            response.data.unchanged ? "内容未变化，上下文世代保持不变" : "专栏课件已更新",
          );
          await load();
        } catch (error: any) {
          message.error(error.response?.data?.detail || "课件上传失败");
        } finally { setBusy(false); }
      },
    });
    return Upload.LIST_IGNORE;
  };

  const generateOutline = async () => {
    if (!current?.source) return;
    setBusy(true);
    try {
      const response = await api.post(`/admin/project-context/sources/${current.source.id}/outline/generate`, undefined, { timeout: 0 });
      setOutlineText(response.data.source.outline_text || "");
      setOutlineEditing(true);
      setOutlineOpen(true);
      await load();
      message.success("总大纲草稿已生成，请校对内容后保存启用");
    } catch (error: any) {
      message.error(error.response?.data?.detail || "总大纲生成失败");
    } finally { setBusy(false); }
  };

  const openOutline = (editing: boolean) => {
    if (!current?.source) return;
    setOutlineText(current.source.outline_text || "");
    setOutlineEditing(editing);
    setOutlineOpen(true);
  };

  const closeOutline = () => {
    setOutlineOpen(false);
    setOutlineEditing(false);
    setOutlineText("");
  };

  const saveOutline = async () => {
    if (!current?.source || !outlineText.trim()) return;
    setBusy(true);
    try {
      await api.put(`/admin/project-context/sources/${current.source.id}/outline`, { outline_text: outlineText });
      message.success("总大纲已保存并启用");
      closeOutline();
      await load();
    } catch (error: any) {
      message.error(error.response?.data?.detail || "总大纲保存失败");
    } finally { setBusy(false); }
  };

  if (loading && !rows.length && !unassigned.length) return <Spin />;

  if (!id) {
    const seriesTable = rows.length ? <Table rowKey="id" dataSource={rows} pagination={false} columns={[
      { title: "专栏", dataIndex: "name" },
      { title: "当前课件", render: (_: unknown, row: Series) => row.source?.filename || "未上传" },
      { title: "视频数", dataIndex: "video_count" },
      { title: "总大纲", render: (_: unknown, row: Series) => { const state = outlineLabels[row.source?.outline_status || "empty"] || { label: "状态未知" }; return <Tag color={state.color}>{state.label}</Tag>; } },
      { title: "操作", render: (_: unknown, row: Series) => <Space>
        <Button onClick={() => navigate(`/admin/columns/${row.id}`)}>进入专栏</Button>
        <Dropdown menu={{ items: [
          { key: "rename", label: "重命名", icon: <EditOutlined />, onClick: () => setNameEditor({ id: row.id, name: row.name }) },
          { type: "divider" },
          { key: "delete", label: "删除专栏", icon: <DeleteOutlined />, danger: true, onClick: () => deleteSeries(row) },
        ] }}><Button aria-label={`${row.name}更多操作`} icon={<MoreOutlined />} /></Dropdown>
      </Space> },
    ]} /> : <Empty description="还没有专栏"><Button type="primary" onClick={() => setNameEditor({ name: "" })}>创建第一个专栏</Button></Empty>;

    const unassignedTable = unassigned.length ? <Table rowKey="course_id" dataSource={unassigned} pagination={false} columns={[
      { title: "课程标识", dataIndex: "course_id" },
      { title: "素材状态", dataIndex: "status", render: (value: string) => <Tag color={value === "ready" ? "green" : "red"}>{value}</Tag> },
      { title: "字幕状态", render: (_: unknown, row: UnassignedVideo) => { const state = deriveSubtitleState(row); return <Tag color={subtitleColors[state.kind]}>{state.label}</Tag>; } },
      { title: "操作", render: (_: unknown, row: UnassignedVideo) => <Button onClick={() => { setAssigning(row); setAssignSeriesId(undefined); }}>归入专栏</Button> },
    ]} /> : <Empty description="没有待归类视频" />;

    return <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <div><Typography.Title level={3} style={{ margin: 0 }}>专栏管理</Typography.Title><Typography.Text type="secondary">专栏是课件、视频、字幕和学习上下文的内容容器</Typography.Text></div>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setNameEditor({ name: "" })}>创建专栏</Button>
      </div>
      <Card><Tabs type="card" className="admin-card-tabs" items={[
        { key: "series", label: "专栏", children: seriesTable },
        { key: "unassigned", label: `待归类视频（${unassigned.length}）`, children: unassignedTable },
      ]} /></Card>
      <Modal title={nameEditor?.id ? "重命名专栏" : "创建专栏"} open={!!nameEditor} confirmLoading={busy} onOk={saveName} onCancel={() => setNameEditor(null)}>
        <Input aria-label="专栏名称" value={nameEditor?.name || ""} onChange={(event) => setNameEditor((value) => value ? { ...value, name: event.target.value } : value)} onPressEnter={() => void saveName()} />
      </Modal>
      <Modal title={assigning ? `将 ${assigning.course_id} 归入专栏` : "归入专栏"} open={!!assigning} okText="确认归类" confirmLoading={busy} okButtonProps={{ disabled: !assignSeriesId }} onCancel={() => { setAssigning(null); setAssignSeriesId(undefined); }} onOk={assignVideo}>
        <Typography.Paragraph type="secondary">归类后请进入目标专栏管理字幕和课程知识。</Typography.Paragraph>
        <Select aria-label="目标专栏" placeholder="请选择专栏" value={assignSeriesId} onChange={setAssignSeriesId} style={{ width: "100%" }} options={rows.map((row) => ({ value: row.id, label: row.name }))} />
      </Modal>
    </Space>;
  }

  if (!current) return <Empty description="专栏不存在或已删除" />;

  const source = current.source;
  const outlineStatus = source?.outline_status || "empty";
  const outlineState = outlineLabels[outlineStatus] || { label: "状态未知" };
  const canEditOutline = outlineStatus === "draft" || outlineStatus === "ready";
  const courseware = <Space direction="vertical" size={16} style={{ width: "100%" }}>
    <Card title="当前课件" extra={source ? <Tag color="green">已上传</Tag> : <Tag>未上传</Tag>}>
      {source ? <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Typography.Text strong>{source.filename}</Typography.Text>
        <Typography.Text type="secondary">{source.page_count} 页 · 上下文世代 {current.context_epoch}</Typography.Text>
        <Space>
          <Upload accept=".pptx" showUploadList={false} beforeUpload={uploadPpt}><Button type="primary" icon={<UploadOutlined />} loading={busy}>更换课件</Button></Upload>
          <Button danger disabled={busy} onClick={() => Modal.confirm({ title: "移除当前课件？", content: "视频和字幕会保留，但专栏上下文将切换世代。", okText: "移除", okButtonProps: { danger: true }, onOk: async () => { try { await api.delete(`/admin/project-context/sources/${source.id}`); message.success("课件已移除"); await load(); } catch (error: any) { message.error(error.response?.data?.detail || "移除失败"); } } })}>移除课件</Button>
        </Space>
      </Space> : <Empty description="该专栏还没有课件"><Upload accept=".pptx" showUploadList={false} beforeUpload={uploadPpt}><Button type="primary" icon={<UploadOutlined />} loading={busy}>上传 PPT</Button></Upload></Empty>}
    </Card>
    <Card title="专栏总大纲" extra={<Tag color={outlineState.color}>{outlineState.label}</Tag>}>
      {!source ? <Empty description="请先上传 PPT 课件" /> : <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Typography.Text type="secondary">关联课件：{source.filename} · 上下文世代 {current.context_epoch}{source.outline_updated_at ? ` · 更新于 ${new Date(source.outline_updated_at).toLocaleString()}` : ""}</Typography.Text>
        {outlineStatus === "stale" && <Alert type="warning" showIcon message="旧大纲基于旧课件，仅供查看，不会进入模型上下文。" />}
        {outlineStatus === "error" && <Alert type="error" showIcon message="最近一次总大纲生成失败，请检查模型状态后重试。" />}
        <Space>
          {(outlineStatus === "empty" || outlineStatus === "error") && <Button type="primary" loading={busy} onClick={generateOutline}>{outlineStatus === "error" ? "重新生成总大纲" : "生成总大纲"}</Button>}
          {outlineStatus === "draft" && <Button type="primary" onClick={() => openOutline(true)}>审核草稿</Button>}
          {outlineStatus === "ready" && <Button onClick={() => openOutline(false)}>查看或编辑大纲</Button>}
          {outlineStatus === "stale" && <>{source.outline_text && <Button onClick={() => openOutline(false)}>查看旧大纲</Button>}<Button type="primary" loading={busy} onClick={generateOutline}>重新生成总大纲</Button></>}
        </Space>
      </Space>}
    </Card>
  </Space>;

  return <Space direction="vertical" size={20} style={{ width: "100%" }}>
    <Button icon={<ArrowLeftOutlined />} onClick={() => navigate("/admin/columns")}>返回专栏列表</Button>
    <div className="admin-series-heading">
      <Typography.Title level={3}>{current.name}</Typography.Title>
      <Typography.Text type="secondary">课件、视频和字幕都在当前专栏内管理</Typography.Text>
    </div>
    <Tabs type="card" className="admin-card-tabs" items={[
      { key: "courseware", label: "课件", children: courseware },
      { key: "videos", label: `视频（${current.video_count}）`, children: <Materials seriesId={current.id} onConfigureKnowledge={(courseId) => navigate(`/admin/columns/${current.id}/videos/${encodeURIComponent(courseId)}`)} /> },
    ]} />
    <Drawer title={`${current.name} · 专栏总大纲`} open={outlineOpen} width={760} destroyOnClose onClose={closeOutline} extra={<Space>{!outlineEditing && canEditOutline && <Button onClick={() => setOutlineEditing(true)}>编辑大纲</Button>}{outlineEditing && <Button type="primary" loading={busy} disabled={!outlineText.trim()} onClick={saveOutline}>保存并启用</Button>}</Space>}>
      <Space direction="vertical" size={16} style={{ width: "100%" }}>
        <Space wrap><Tag color={outlineState.color}>{outlineState.label}</Tag><Typography.Text type="secondary">关联课件：{source?.filename}</Typography.Text><Typography.Text type="secondary">上下文世代：{current.context_epoch}</Typography.Text></Space>
        {outlineStatus === "stale" && <Alert type="warning" showIcon message="该大纲已失效，仅供查看，不会进入模型上下文。" />}
        {outlineEditing ? <Input.TextArea aria-label="专栏总大纲" value={outlineText} onChange={(event) => setOutlineText(event.target.value)} autoSize={{ minRows: 18, maxRows: 30 }} /> : outlineText ? <div className="ai-answer"><ReactMarkdown remarkPlugins={[remarkGfm]}>{outlineText}</ReactMarkdown></div> : <Empty description="暂无大纲内容" />}
      </Space>
    </Drawer>
  </Space>;
}
