import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  Upload,
  message,
} from "antd";
import {
  CloudUploadOutlined,
  DeleteOutlined,
  FileTextOutlined,
  ReloadOutlined,
  SaveOutlined,
} from "@ant-design/icons";
import type { UploadFile } from "antd";
import { api } from "../../api/client";

interface ProjectSource {
  id: number;
  filename: string;
  format: string;
  sha256: string;
  status: string;
  created_at?: string | null;
  page_count: number;
}

interface PptPage {
  page: number;
  title: string;
  text: string;
}

interface VideoKnowledge {
  course_id: string;
  video_name: string;
  course_type: "theory" | "practice";
  source_id: number | null;
  source_filename: string | null;
  page_start: number | null;
  page_end: number | null;
  knowledge_text: string;
  knowledge_filename: string | null;
  outline_text: string;
  outline_status: string;
  subtitle_included: boolean;
  legacy_context?: boolean;
}

interface ContextVersion {
  id: number;
  version: number;
  summary_text: string;
  status: string;
  is_stale: boolean;
  updated_at?: string | null;
  published_at?: string | null;
}

interface ProjectContextData {
  project: { project_key: string; name: string };
  sources: ProjectSource[];
  published: ContextVersion | null;
  draft: ContextVersion | null;
  material_count: number;
  videos: VideoKnowledge[];
}

export default function ProjectContext() {
  const [data, setData] = useState<ProjectContextData | null>(null);
  const [draftText, setDraftText] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [videoModalOpen, setVideoModalOpen] = useState(false);
  const [selectedVideo, setSelectedVideo] = useState<VideoKnowledge | null>(null);
  const [selectedSourceId, setSelectedSourceId] = useState<number | null>(null);
  const [pageStart, setPageStart] = useState<number | null>(null);
  const [pageEnd, setPageEnd] = useState<number | null>(null);
  const [pages, setPages] = useState<PptPage[]>([]);
  const [outlineText, setOutlineText] = useState("");
  const sourceRequestSequence = useRef(0);
  const mappingDirty = selectedVideo !== null && (
    selectedSourceId !== selectedVideo.source_id
    || pageStart !== selectedVideo.page_start
    || pageEnd !== selectedVideo.page_end
  );

  const load = async () => {
    setLoading(true);
    try {
      const response = await api.get<ProjectContextData>("/admin/project-context");
      setData(response.data);
      setDraftText(response.data.draft?.summary_text || "");
    } catch {
      message.error("项目背景加载失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const generateDraft = async () => {
    setBusy(true);
    try {
      // 摘要生成需要调用真实模型，耗时可能超过全局 30 秒请求限制。
      await api.post("/admin/project-context/summary/generate", undefined, { timeout: 0 });
      message.success("项目背景摘要草稿已生成，请审核后发布");
      await load();
    } catch (error: any) {
      message.error(error.response?.data?.detail || "摘要生成失败");
    } finally {
      setBusy(false);
    }
  };

  const uploadSource = async () => {
    const file = fileList[0]?.originFileObj;
    if (!file) {
      message.warning("请选择项目资料");
      return;
    }
    const form = new FormData();
    form.append("file", file);
    setBusy(true);
    try {
      await api.post("/admin/project-context/sources", form, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      setFileList([]);
      await load();
      message.success("课件已上传。请为需要上下文的视频选择对应 PPT 页区间");
    } catch (error: any) {
      message.error(error.response?.data?.detail || "资料上传失败");
    } finally {
      setBusy(false);
    }
  };

  const loadSourcePages = async (sourceId: number, video?: VideoKnowledge) => {
    const requestSequence = ++sourceRequestSequence.current;
    setSelectedSourceId(sourceId);
    if (video?.source_id !== sourceId) {
      setPages([]);
      setPageStart(null);
      setPageEnd(null);
    }
    try {
      const response = await api.get(`/admin/project-context/sources/${sourceId}/pages`);
      if (requestSequence !== sourceRequestSequence.current) return;
      const nextPages: PptPage[] = response.data.pages || [];
      setPages(nextPages);
      setPageStart(video?.source_id === sourceId ? video.page_start : nextPages[0]?.page || null);
      setPageEnd(video?.source_id === sourceId ? video.page_end : nextPages[0]?.page || null);
    } catch (error: any) {
      if (requestSequence !== sourceRequestSequence.current) return;
      message.error(error.response?.data?.detail || "PPT 页内容加载失败");
    }
  };

  const openVideoKnowledge = async (video: VideoKnowledge) => {
    setSelectedVideo(video);
    setOutlineText(video.outline_text || "");
    setPages([]);
    setSelectedSourceId(video.source_id);
    setPageStart(video.page_start);
    setPageEnd(video.page_end);
    setVideoModalOpen(true);
    const defaultSource = video.source_id || data?.sources.find((source) => source.format === "pptx")?.id;
    if (defaultSource) await loadSourcePages(defaultSource, video);
  };

  const updateSelectedVideo = (video: VideoKnowledge) => {
    setSelectedVideo(video);
    setOutlineText(video.outline_text || "");
    setData((current) => current ? {
      ...current,
      videos: current.videos.map((item) => item.course_id === video.course_id ? video : item),
    } : current);
  };

  const changeCourseType = async (courseType: "theory" | "practice") => {
    if (!selectedVideo) return;
    setBusy(true);
    try {
      const response = await api.put(
        `/admin/project-context/videos/${encodeURIComponent(selectedVideo.course_id)}/course-type`,
        { course_type: courseType },
      );
      updateSelectedVideo(response.data.video);
    } catch (error: any) {
      message.error(error.response?.data?.detail || "课程类型保存失败");
    } finally {
      setBusy(false);
    }
  };

  const extractVideoKnowledge = async () => {
    if (!selectedVideo || !selectedSourceId || !pageStart || !pageEnd) {
      message.warning("请选择 PPT 和页码区间");
      return;
    }
    setBusy(true);
    try {
      const response = await api.put(
        `/admin/project-context/videos/${encodeURIComponent(selectedVideo.course_id)}/knowledge`,
        {
          source_id: selectedSourceId,
          page_start: pageStart,
          page_end: pageEnd,
          course_type: selectedVideo.course_type,
        },
      );
      updateSelectedVideo(response.data.video);
      message.success("课程知识文本已生成；字幕内容将在后续版本合并");
    } catch (error: any) {
      message.error(error.response?.data?.detail || "课程知识文本生成失败");
    } finally {
      setBusy(false);
    }
  };

  const generateVideoOutline = async () => {
    if (mappingDirty) {
      Modal.info({
        title: "请先更新课程文本",
        content: "PPT 或页码已经改变，请先点击“生成课程文本”，再根据新文本生成大纲。",
      });
      return;
    }
    if (!selectedVideo?.knowledge_text) {
      Modal.info({
        title: "请先生成课程知识文本",
        content: "选择该视频对应的 PPT 单页或页码区间，再生成大纲。",
      });
      return;
    }
    setBusy(true);
    try {
      const response = await api.post(
        `/admin/project-context/videos/${encodeURIComponent(selectedVideo.course_id)}/outline/generate`,
        undefined,
        { timeout: 0 },
      );
      updateSelectedVideo(response.data.video);
      message.success("视频课程大纲已生成");
    } catch (error: any) {
      message.error(error.response?.data?.detail || "视频课程大纲生成失败");
    } finally {
      setBusy(false);
    }
  };

  const saveVideoOutline = async () => {
    if (!selectedVideo) return;
    setBusy(true);
    try {
      const response = await api.put(
        `/admin/project-context/videos/${encodeURIComponent(selectedVideo.course_id)}/outline`,
        { outline_text: outlineText },
      );
      updateSelectedVideo(response.data.video);
      message.success(outlineText.trim() ? "视频大纲已保存" : "视频大纲已清空");
    } catch (error: any) {
      message.error(error.response?.data?.detail || "视频大纲保存失败");
    } finally {
      setBusy(false);
    }
  };

  const deleteSource = async (id: number) => {
    try {
      await api.delete(`/admin/project-context/sources/${id}`);
      message.success("项目资料已删除，摘要已标记为待更新");
      await load();
    } catch (error: any) {
      message.error(error.response?.data?.detail || "删除失败");
    }
  };

  const saveDraft = async () => {
    if (!data?.draft) return;
    setBusy(true);
    try {
      await api.put("/admin/project-context/summary/draft", {
        version_id: data.draft.id,
        summary_text: draftText,
      });
      message.success("摘要草稿已保存");
      await load();
    } catch (error: any) {
      message.error(error.response?.data?.detail || "草稿保存失败");
    } finally {
      setBusy(false);
    }
  };

  const createManualDraft = async () => {
    setBusy(true);
    try {
      await api.put("/admin/project-context/summary/draft", {
        version_id: null,
        summary_text: [
          "# 项目定位",
          "请填写项目解决的问题和使用场景。",
          "\n# 目标",
          "请填写项目阶段目标。",
          "\n# 关键术语与架构",
          "请填写已确认术语和架构。",
          "\n# 关键约束与边界",
          "请填写不能改变的约束和资料未规定的事项。",
          "\n# 来源清单",
          "请根据已上传资料核对来源。",
        ].join("\n"),
      });
      message.success("人工摘要草稿已创建，请结合资料审核填写");
      await load();
    } catch (error: any) {
      message.error(error.response?.data?.detail || "人工草稿创建失败");
    } finally {
      setBusy(false);
    }
  };

  const publishDraft = async () => {
    if (!data?.draft) return;
    setBusy(true);
    try {
      await api.post("/admin/project-context/summary/publish", { version_id: data.draft.id });
      message.success("项目背景摘要已发布，新会话将使用新版本");
      await load();
    } catch (error: any) {
      message.error(error.response?.data?.detail || "发布失败");
    } finally {
      setBusy(false);
    }
  };

  if (loading && !data) {
    return <Spin />;
  }

  const sourceColumns = [
    { title: "资料名", dataIndex: "filename" },
    { title: "格式", dataIndex: "format", render: (value: string) => <Tag>{value.toUpperCase()}</Tag> },
    { title: "PPT 页数", dataIndex: "page_count", render: (value: number) => value || "—" },
    {
      title: "内容指纹",
      dataIndex: "sha256",
      render: (value: string) => <Typography.Text code>{value.slice(0, 12)}…</Typography.Text>,
    },
    {
      title: "操作",
      render: (_: unknown, row: ProjectSource) => (
        <Popconfirm title="已绑定视频的课件不能删除，确认尝试删除？" onConfirm={() => deleteSource(row.id)}>
          <Button danger size="small" icon={<DeleteOutlined />}>删除</Button>
        </Popconfirm>
      ),
    },
  ];

  const videoColumns = [
    { title: "视频", dataIndex: "video_name", ellipsis: true },
    { title: "课程 ID", dataIndex: "course_id" },
    {
      title: "课程类型",
      dataIndex: "course_type",
      render: (value: string, row: VideoKnowledge) => row.legacy_context ? (
        <Tag color="orange">未分类（旧上下文）</Tag>
      ) : (
        <Tag color={value === "practice" ? "blue" : "default"}>
          {value === "practice" ? "实战/案例" : "理论/通用"}
        </Tag>
      ),
    },
    {
      title: "PPT 页区间",
      render: (_: unknown, row: VideoKnowledge) => (
        row.page_start ? `${row.source_filename} · ${row.page_start}–${row.page_end} 页` : "未选择"
      ),
    },
    {
      title: "课程文本",
      render: (_: unknown, row: VideoKnowledge) => row.legacy_context ? (
        <Tag color="orange">未分类（旧上下文）</Tag>
      ) : (
        <Tag color={row.knowledge_text ? "green" : "default"}>
          {row.knowledge_text ? row.knowledge_filename || "已生成" : "待生成"}
        </Tag>
      ),
    },
    {
      title: "视频大纲",
      render: (_: unknown, row: VideoKnowledge) => (
        <Tag color={row.outline_status === "ready" ? "green" : row.outline_status === "draft" ? "orange" : "default"}>
          {row.outline_status === "ready" ? "已启用" : row.outline_status === "draft" ? "待审核" : "无大纲"}
        </Tag>
      ),
    },
    {
      title: "操作",
      render: (_: unknown, row: VideoKnowledge) => (
        <Button size="small" onClick={() => void openVideoKnowledge(row)}>配置课程知识</Button>
      ),
    },
  ];

  const pagePreview = pages
    .filter((page) => pageStart !== null && pageEnd !== null && page.page >= pageStart && page.page <= pageEnd)
    .map((page) => page.text)
    .join("\n\n");

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <div>
        <Typography.Title level={3} style={{ marginBottom: 4 }}>专栏知识</Typography.Title>
        <Typography.Text type="secondary">
          一份 PPT 可以映射多个视频；理论课程默认不使用大纲，实战/案例课程只发送已启用的视频大纲。
        </Typography.Text>
      </div>

      <Card>
        <Descriptions size="small" column={3}>
          <Descriptions.Item label="默认专栏">{data?.project.name}</Descriptions.Item>
          <Descriptions.Item label="专栏 Key">{data?.project.project_key}</Descriptions.Item>
          <Descriptions.Item label="已关联视频">{data?.material_count || 0}</Descriptions.Item>
        </Descriptions>
      </Card>

      <Card title="专栏共享课件" extra={<Typography.Text type="secondary">视频页区间当前使用 PPTX；单文件不超过 50MB</Typography.Text>}>
        <Space wrap style={{ marginBottom: 16 }}>
          <Upload
            beforeUpload={() => false}
            maxCount={1}
            fileList={fileList}
            onChange={({ fileList: next }) => setFileList(next.slice(-1))}
            accept=".md,.pdf,.pptx"
          >
            <Button icon={<FileTextOutlined />}>选择资料</Button>
          </Upload>
          <Button type="primary" icon={<CloudUploadOutlined />} loading={busy} onClick={uploadSource}>
            上传资料
          </Button>
        </Space>
        {data?.sources.length ? (
          <Table rowKey="id" columns={sourceColumns} dataSource={data.sources} pagination={false} />
        ) : (
          <Empty description="尚未上传项目资料" />
        )}
      </Card>

      <Card
        title="专栏视频与课程知识"
        extra={<Typography.Text type="secondary">首版仅提取课件文本，字幕合并后续接入</Typography.Text>}
      >
        {data?.videos?.length ? (
          <Table rowKey="course_id" columns={videoColumns} dataSource={data.videos} pagination={false} />
        ) : (
          <Empty description="请先在素材管理上传视频" />
        )}
      </Card>

      <Card
        title="旧版专栏公共摘要（兼容）"
        extra={data?.published && <Tag color={data.published.is_stale ? "orange" : "green"}>v{data.published.version}</Tag>}
      >
        {data?.published ? (
          <Typography.Paragraph style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}>
            {data.published.summary_text}
          </Typography.Paragraph>
        ) : (
          <Empty description="尚无已审核发布的项目背景" />
        )}
      </Card>

      <Card
        title="旧版公共摘要草稿（兼容）"
        extra={<Button icon={<ReloadOutlined />} loading={busy} onClick={generateDraft}>AI 生成草稿</Button>}
      >
        {data?.draft ? (
          <Space direction="vertical" style={{ width: "100%" }}>
            <Alert type="info" showIcon message={`草稿 v${data.draft.version} 不会自动进入学习问答`} />
            <Input.TextArea
              value={draftText}
              onChange={(event) => setDraftText(event.target.value)}
              autoSize={{ minRows: 10, maxRows: 24 }}
              showCount
            />
            <Space>
              <Button icon={<SaveOutlined />} loading={busy} onClick={saveDraft}>保存草稿</Button>
              <Popconfirm title="发布后，新会话将固定使用此版本。确认发布？" onConfirm={publishDraft}>
                <Button type="primary" loading={busy}>审核通过并发布</Button>
              </Popconfirm>
            </Space>
          </Space>
        ) : (
          <Empty
            description="上传资料后，可由 AI 生成；资料超过生成预算时请创建人工草稿"
          >
            <Button loading={busy} onClick={createManualDraft}>新建人工草稿</Button>
          </Empty>
        )}
      </Card>

      <Modal
        title={selectedVideo ? `${selectedVideo.video_name} · 课程知识` : "课程知识"}
        open={videoModalOpen}
        width={1120}
        footer={null}
        onCancel={() => setVideoModalOpen(false)}
        destroyOnHidden
      >
        {selectedVideo && (
          <Space direction="vertical" size={16} style={{ width: "100%" }}>
            <Alert
              type={selectedVideo.course_type === "practice" ? "info" : "success"}
              showIcon
              message={
                selectedVideo.course_type === "practice"
                  ? "实战/案例：只有保存启用后的当前视频大纲会发送给 AI"
                  : "理论/通用：视频可以正常提问，但不会发送课程大纲"
              }
            />
            <Space wrap>
              <Typography.Text strong>课程类型</Typography.Text>
              <Select
                style={{ width: 220 }}
                value={selectedVideo.course_type}
                disabled={busy}
                onChange={(value) => void changeCourseType(value)}
                options={[
                  { value: "theory", label: "理论/通用（不发送大纲）" },
                  { value: "practice", label: "实战/案例（可发送大纲）" },
                ]}
              />
              <Typography.Text strong>共享 PPT</Typography.Text>
              <Select
                style={{ width: 240 }}
                placeholder="选择 PPT"
                value={selectedSourceId || undefined}
                onChange={(value) => void loadSourcePages(value)}
                options={(data?.sources || [])
                  .filter((source) => source.format === "pptx")
                  .map((source) => ({ value: source.id, label: `${source.filename}（${source.page_count} 页）` }))}
              />
              <Typography.Text strong>页码</Typography.Text>
              <InputNumber
                aria-label="PPT 起始页"
                min={pages[0]?.page || 1}
                max={pages[pages.length - 1]?.page}
                value={pageStart}
                onChange={(value) => setPageStart(value)}
              />
              <Typography.Text>至</Typography.Text>
              <InputNumber
                aria-label="PPT 结束页"
                min={pages[0]?.page || 1}
                max={pages[pages.length - 1]?.page}
                value={pageEnd}
                onChange={(value) => setPageEnd(value)}
              />
              <Button type="primary" loading={busy} onClick={extractVideoKnowledge}>
                生成课程文本
              </Button>
            </Space>

            {!pages.length && (
              <Empty description="尚未关联 PPT，请先选择共享课件和页码区间" />
            )}

            <Row gutter={16}>
              <Col xs={24} lg={12}>
                <Card title="课件文本（左）" size="small">
                  {mappingDirty && (
                    <Alert
                      type="warning"
                      showIcon
                      message="当前是未保存的新页预览；请先生成课程文本，再操作右侧大纲"
                      style={{ marginBottom: 12 }}
                    />
                  )}
                  <Input.TextArea
                    aria-label="课件文本"
                    readOnly
                    value={pagePreview || selectedVideo.knowledge_text}
                    placeholder="选择 PPT 页区间后在这里预览；点击生成后写入视频文件夹"
                    autoSize={{ minRows: 16, maxRows: 28 }}
                  />
                </Card>
              </Col>
              <Col xs={24} lg={12}>
                <Card
                  title="视频大纲（右）"
                  size="small"
                  extra={selectedVideo.course_type === "practice" && (
                    <Button disabled={mappingDirty} loading={busy} onClick={generateVideoOutline}>AI 生成大纲</Button>
                  )}
                >
                  {selectedVideo.course_type === "theory" ? (
                    <Empty description="理论/通用课程默认不需要大纲；如有项目案例，请先切换为实战/案例" />
                  ) : selectedVideo.knowledge_text ? (
                    <Space direction="vertical" style={{ width: "100%" }}>
                      {!selectedVideo.outline_text && (
                        <Empty description="还没有大纲，是否根据左侧课程文本生成？">
                          <Button type="primary" disabled={mappingDirty} loading={busy} onClick={generateVideoOutline}>需要，生成大纲</Button>
                        </Empty>
                      )}
                      <Input.TextArea
                        aria-label="视频大纲"
                        value={outlineText}
                        onChange={(event) => setOutlineText(event.target.value)}
                        placeholder="AI 生成后可人工修改；保存后才会用于新提问"
                        autoSize={{ minRows: 16, maxRows: 28 }}
                        showCount
                      />
                      <Button icon={<SaveOutlined />} disabled={mappingDirty} loading={busy} onClick={saveVideoOutline}>
                        保存并启用
                      </Button>
                    </Space>
                  ) : (
                    <Empty description="尚未生成课程文本，请先选择 PPT 页区间">
                      <Button onClick={extractVideoKnowledge}>先生成课程文本</Button>
                    </Empty>
                  )}
                </Card>
              </Col>
            </Row>
          </Space>
        )}
      </Modal>
    </Space>
  );
}
