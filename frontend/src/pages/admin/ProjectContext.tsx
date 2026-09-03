import { useEffect, useMemo, useState } from "react";
import {
  Alert, Button, Card, Col, Empty, Input, InputNumber, Modal, Popconfirm, Row,
  Select, Space, Spin, Table, Tag, Typography, Upload, message,
} from "antd";
import { DeleteOutlined, EditOutlined, UploadOutlined } from "@ant-design/icons";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { api } from "../../api/client";

interface ProjectSource {
  id: number; filename: string; column_name: string; format: string; sha256: string;
  page_count: number; upload_status: string; outline_text: string;
  outline_status: "empty" | "draft" | "ready" | "stale" | "error";
}
interface VideoKnowledge {
  course_id: string; video_name: string; course_type: "theory" | "practice";
  source_id: number | null; source_filename: string | null; page_start: number | null;
  page_end: number | null; knowledge_text: string;
  knowledge_status: "unassigned" | "pending" | "stale" | "ready";
}
interface PptPage { page: number; title: string; text: string; }
interface ProjectContextData {
  project: { project_key: string; name: string }; sources: ProjectSource[]; videos: VideoKnowledge[];
}

const outlineLabel = {
  empty: { text: "未生成", color: undefined }, draft: { text: "待审核", color: "orange" },
  ready: { text: "已启用", color: "green" }, stale: { text: "待重新生成", color: "red" },
  error: { text: "生成失败", color: "red" },
} as const;
const knowledgeLabel = {
  unassigned: { text: "待归类", color: "orange" }, pending: { text: "待配置", color: undefined },
  stale: { text: "课件已更新，待重新生成", color: "red" },
  ready: { text: "已就绪", color: "green" },
} as const;

export default function ProjectContext() {
  const navigate = useNavigate();
  const location = useLocation();
  const { sourceId: sourceIdParam, courseId } = useParams();
  const sourceId = sourceIdParam ? Number(sourceIdParam) : null;
  const [data, setData] = useState<ProjectContextData | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [outlineEditor, setOutlineEditor] = useState<ProjectSource | null>(null);
  const [outlineText, setOutlineText] = useState("");
  const [pages, setPages] = useState<PptPage[]>([]);
  const [pageStart, setPageStart] = useState<number | null>(null);
  const [pageEnd, setPageEnd] = useState<number | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const response = await api.get<ProjectContextData>("/admin/project-context");
      setData(response.data);
    } catch { message.error("专栏数据加载失败"); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, []);

  const pptSources = useMemo(
    () => (data?.sources || []).filter((item) => item.format === "pptx"), [data],
  );
  const source = pptSources.find((item) => item.id === sourceId);
  const video = data?.videos.find((item) => item.course_id === courseId);

  useEffect(() => {
    if (!sourceId || !courseId) return;
    api.get(`/admin/project-context/sources/${sourceId}/pages`).then((response) => {
      const nextPages = response.data.pages || [];
      setPages(nextPages);
      setPageStart(video?.page_start || nextPages[0]?.page || null);
      setPageEnd(video?.page_end || nextPages[0]?.page || null);
    }).catch((error) => message.error(error.response?.data?.detail || "PPT 页内容加载失败"));
  }, [sourceId, courseId, video?.page_start, video?.page_end]);

  const openOutlineEditor = (item: ProjectSource) => {
    setOutlineEditor(item); setOutlineText(item.outline_text || "");
  };
  const generateOutline = async (id: number) => {
    setBusy(true);
    try {
      const response = await api.post(`/admin/project-context/sources/${id}/outline/generate`, undefined, { timeout: 0 });
      message.success("专栏总大纲草稿已生成，请审核后启用");
      openOutlineEditor(response.data.source); await load();
    } catch (error: any) { message.error(error.response?.data?.detail || "专栏总大纲生成失败"); }
    finally { setBusy(false); }
  };
  const saveOutline = async () => {
    if (!outlineEditor) return;
    setBusy(true);
    try {
      await api.put(`/admin/project-context/sources/${outlineEditor.id}/outline`, { outline_text: outlineText });
      message.success(outlineText.trim() ? "专栏总大纲已保存并启用" : "专栏总大纲已清空");
      setOutlineEditor(null); await load();
    } catch (error: any) { message.error(error.response?.data?.detail || "专栏总大纲保存失败"); }
    finally { setBusy(false); }
  };
  const askGenerateOutline = (item: ProjectSource) => new Promise<void>((resolve) => {
    Modal.confirm({
      title: "是否生成整份 PPT 专栏总大纲？",
      content: "AI 会读取整份课件生成草稿，草稿经你审核并启用后才进入学习问答。",
      okText: "生成草稿",
      cancelText: "稍后处理",
      onOk: async () => { await generateOutline(item.id); resolve(); },
      onCancel: () => resolve(),
    });
  });

  const uploadCourseware = (file: File) => {
    const duplicate = pptSources.find((item) => item.filename.toLocaleLowerCase() === file.name.toLocaleLowerCase());
    Modal.confirm({
      title: duplicate ? "覆盖同名课件？" : "上传课件？",
      content: duplicate
        ? "将保留原专栏和视频归属，但总大纲及已映射视频课程文本会失效。"
        : `确认将 ${file.name} 上传为一个新专栏？`,
      okText: duplicate ? "确认覆盖" : "确认上传",
      onOk: async () => {
        const form = new FormData(); form.append("file", file); setBusy(true);
        try {
          const config = { headers: { "Content-Type": "multipart/form-data" } };
          const response = duplicate
            ? await api.put(`/admin/project-context/sources/${duplicate.id}`, form, config)
            : await api.post("/admin/project-context/sources", form, config);
          const uploaded = response.data.source as ProjectSource;
          message[response.data.unchanged ? "info" : "success"](
            response.data.unchanged ? "文件内容未变化，现有上下文继续有效" : duplicate ? "课件已覆盖" : "课件已上传",
          );
          await load();
          if (!response.data.unchanged) await askGenerateOutline(uploaded);
          if (duplicate && response.data.affected_video_count) Modal.info({
            title: "关联视频课程文本已失效",
            content: `共有 ${response.data.affected_video_count} 个视频需要重新生成课程文本。`,
            okText: "前往处理", onOk: () => navigate(`/admin/columns/${duplicate.id}`),
          });
        } catch (error: any) {
          const detail = error.response?.data?.detail;
          message.error(typeof detail === "string" ? detail : detail?.message || "课件上传失败");
        } finally { setBusy(false); }
      },
    });
    return Upload.LIST_IGNORE;
  };
  const deleteSource = async (id: number) => {
    try { await api.delete(`/admin/project-context/sources/${id}`); message.success("课件已删除"); await load(); }
    catch (error: any) { message.error(error.response?.data?.detail || "删除失败"); }
  };
  const changeCourseType = async (item: VideoKnowledge, courseType: "theory" | "practice") => {
    try {
      await api.put(`/admin/project-context/videos/${encodeURIComponent(item.course_id)}/course-type`, { course_type: courseType });
      await load();
    } catch (error: any) { message.error(error.response?.data?.detail || "课程类型保存失败"); }
  };
  const buildCourseText = async () => {
    if (!video || !sourceId || !pageStart || !pageEnd) return;
    setBusy(true);
    try {
      await api.put(`/admin/project-context/videos/${encodeURIComponent(video.course_id)}/knowledge`, {
        source_id: sourceId, page_start: pageStart, page_end: pageEnd, course_type: video.course_type,
      });
      message.success("当前视频课程文本已生成"); await load();
    } catch (error: any) { message.error(error.response?.data?.detail || "课程文本生成失败"); }
    finally { setBusy(false); }
  };

  if (loading && !data) return <Spin />;
  const editor = <Modal
    title={outlineEditor ? `${outlineEditor.column_name} · 专栏总大纲草稿审核` : "专栏总大纲"}
    open={!!outlineEditor} width={900} okText="保存并启用" confirmLoading={busy}
    onOk={saveOutline} onCancel={() => setOutlineEditor(null)} destroyOnHidden
  >
    <Alert type="info" showIcon message="AI 结果仅是草稿；只有点击“保存并启用”后才会进入学习问答。" style={{ marginBottom: 12 }} />
    <Input.TextArea aria-label="专栏总大纲" value={outlineText} onChange={(event) => setOutlineText(event.target.value)} autoSize={{ minRows: 18, maxRows: 30 }} />
  </Modal>;

  if (location.pathname.endsWith("/courseware")) {
    return <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <div><Typography.Title level={3} style={{ marginBottom: 4 }}>上传课件</Typography.Title><Typography.Text type="secondary">一个 PPTX 对应一个专栏；单文件不超过 50MB</Typography.Text></div>
      <Card title="专栏课件">
        <Upload accept=".pptx" showUploadList={false} beforeUpload={uploadCourseware}>
          <Button type="primary" icon={<UploadOutlined />} loading={busy}>选择并上传课件</Button>
        </Upload>
        <div style={{ marginTop: 16 }}>{pptSources.length ? <Table rowKey="id" dataSource={pptSources} pagination={false} columns={[
          { title: "专栏", dataIndex: "column_name" }, { title: "课件", dataIndex: "filename" },
          { title: "PPT 页数", dataIndex: "page_count" }, { title: "上传状态", render: () => <Tag color="green">已上传</Tag> },
          { title: "总大纲状态", render: (_: unknown, item: ProjectSource) => { const status = outlineLabel[item.outline_status]; return <Tag color={status.color}>{status.text}</Tag>; } },
          { title: "操作", render: (_: unknown, item: ProjectSource) => <Space>
            <Button size="small" loading={busy} onClick={() => item.outline_text ? openOutlineEditor(item) : void generateOutline(item.id)}>{item.outline_text ? "编辑总大纲" : "生成总大纲"}</Button>
            <Popconfirm title="被视频引用的课件不能删除，确认尝试删除？" onConfirm={() => deleteSource(item.id)}><Button danger size="small" icon={<DeleteOutlined />}>删除</Button></Popconfirm>
          </Space> },
        ]} /> : <Empty description="尚未上传 PPT 课件" />}</div>
      </Card>{editor}
    </Space>;
  }

  if (!sourceId) {
    const unassigned = data?.videos.filter((item) => !item.source_id) || [];
    return <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Typography.Title level={3} style={{ margin: 0 }}>专栏视频</Typography.Title>
      <Card>{pptSources.length ? <Table rowKey="id" dataSource={pptSources} pagination={false} columns={[
        { title: "专栏", dataIndex: "column_name" }, { title: "课件", dataIndex: "filename" },
        { title: "视频数", render: (_: unknown, item: ProjectSource) => data?.videos.filter((videoItem) => videoItem.source_id === item.id).length || 0 },
        { title: "总大纲", render: (_: unknown, item: ProjectSource) => { const status = outlineLabel[item.outline_status]; return <Tag color={status.color}>{status.text}</Tag>; } },
        { title: "操作", render: (_: unknown, item: ProjectSource) => <Button size="small" onClick={() => navigate(`/admin/columns/${item.id}`)}>查看专栏视频</Button> },
      ]} /> : <Empty description="请先上传 PPT 课件" />}</Card>
      {!!unassigned.length && <Card title="待归类旧视频"><Table rowKey="course_id" dataSource={unassigned} pagination={false} columns={[
        { title: "视频", dataIndex: "video_name" }, { title: "课程 ID", dataIndex: "course_id" }, { title: "状态", render: () => <Tag color="orange">待归类</Tag> },
      ]} /></Card>}
    </Space>;
  }
  if (!source) return <Empty description="专栏不存在或已删除" />;

  if (!courseId) {
    const sourceVideos = data?.videos.filter((item) => item.source_id === source.id) || [];
    return <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Button type="link" style={{ padding: 0 }} onClick={() => navigate("/admin/columns")}>← 返回专栏列表</Button>
      <Typography.Title level={3} style={{ margin: 0 }}>{source.column_name}</Typography.Title>
      <Typography.Text type="secondary">课程类型只用于管理分类，不影响课件上下文是否发送。</Typography.Text>
      <Card title="专栏视频">{sourceVideos.length ? <Table rowKey="course_id" dataSource={sourceVideos} pagination={false} columns={[
        { title: "视频", dataIndex: "video_name", ellipsis: true }, { title: "课程 ID", dataIndex: "course_id" },
        { title: "课程类型", render: (_: unknown, item: VideoKnowledge) => <Select value={item.course_type} style={{ width: 140 }} onChange={(value) => void changeCourseType(item, value)} options={[{ value: "theory", label: "理论/通用" }, { value: "practice", label: "实战/案例" }]} /> },
        { title: "PPT 页区间", render: (_: unknown, item: VideoKnowledge) => item.page_start ? `${item.page_start}–${item.page_end} 页` : "未选择" },
        { title: "课程文本", render: (_: unknown, item: VideoKnowledge) => { const status = knowledgeLabel[item.knowledge_status]; return <Tag color={status.color}>{status.text}</Tag>; } },
        { title: "操作", render: (_: unknown, item: VideoKnowledge) => <Button size="small" onClick={() => navigate(`/admin/columns/${source.id}/videos/${encodeURIComponent(item.course_id)}`)}>配置课程知识</Button> },
      ]} /> : <Empty description="该专栏还没有视频，请在素材管理上传视频并选择此专栏" />}</Card>
    </Space>;
  }

  if (!video) return <Empty description="视频不存在" />;
  const preview = pages.filter((page) => pageStart !== null && pageEnd !== null && page.page >= pageStart && page.page <= pageEnd).map((page) => page.text).join("\n\n");
  return <Space direction="vertical" size={16} style={{ width: "100%" }}>
    <Button type="link" style={{ padding: 0 }} onClick={() => navigate(`/admin/columns/${source.id}`)}>← 返回 {source.column_name}</Button>
    <Typography.Title level={3} style={{ margin: 0 }}>{video.video_name} · 课程知识</Typography.Title>
    <Card><Space wrap>
      <Typography.Text strong>课程类型</Typography.Text><Select value={video.course_type} style={{ width: 140 }} onChange={(value) => void changeCourseType(video, value)} options={[{ value: "theory", label: "理论/通用" }, { value: "practice", label: "实战/案例" }]} />
      <Typography.Text strong>PPT 页码</Typography.Text><InputNumber min={pages[0]?.page || 1} max={pages[pages.length - 1]?.page} value={pageStart} onChange={setPageStart} /><Typography.Text>至</Typography.Text><InputNumber min={pages[0]?.page || 1} max={pages[pages.length - 1]?.page} value={pageEnd} onChange={setPageEnd} />
      <Button type="primary" loading={busy} onClick={buildCourseText}>生成课程文本</Button>
    </Space></Card>
    <Row gutter={16}><Col xs={24} lg={12}><Card title="当前视频 PPT 页原文"><Input.TextArea readOnly value={preview || video.knowledge_text} autoSize={{ minRows: 20, maxRows: 32 }} placeholder="请选择页区间并生成课程文本" /></Card></Col>
      <Col xs={24} lg={12}><Card title="整份 PPT 专栏总大纲" extra={<Space><Tag color={outlineLabel[source.outline_status].color}>{outlineLabel[source.outline_status].text}</Tag>{source.outline_text ? <Button icon={<EditOutlined />} onClick={() => openOutlineEditor(source)}>编辑</Button> : null}</Space>}>{source.outline_status === "stale" && <Alert type="warning" showIcon message="课件已更新，旧大纲仅供参考，不会进入问答。" style={{ marginBottom: 12 }} />}{source.outline_text ? <Typography.Paragraph style={{ whiteSpace: "pre-wrap" }}>{source.outline_text}</Typography.Paragraph> : <Empty description="尚未生成专栏总大纲"><Button type="primary" loading={busy} onClick={() => void generateOutline(source.id)}>AI 生成草稿</Button></Empty>}</Card></Col>
    </Row>{editor}
  </Space>;
}
