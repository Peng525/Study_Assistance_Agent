import { useEffect, useState } from "react";
import { Alert, Button, Card, Empty, Input, Popconfirm, Space, Spin, Table, Tag, Typography, Upload, message } from "antd";
import { DeleteOutlined, UploadOutlined } from "@ant-design/icons";
import type { UploadFile } from "antd";
import { api } from "../../api/client";

interface Source { id: number; filename: string; format: string; sha256: string; page_count: number; }
interface Version { id: number; version: number; summary_text: string; is_stale: boolean; }
interface Data { project: { name: string; project_key: string }; sources: Source[]; published: Version | null; draft: Version | null; }

export default function ProjectBackground() {
  const [data, setData] = useState<Data | null>(null);
  const [draftText, setDraftText] = useState("");
  const [files, setFiles] = useState<UploadFile[]>([]);
  const [busy, setBusy] = useState(false);
  const load = async () => { try { const result = (await api.get<Data>("/admin/project-context")).data; setData(result); setDraftText(result.draft?.summary_text || ""); } catch { message.error("项目背景加载失败"); } };
  useEffect(() => { void load(); }, []);
  const upload = async () => {
    const file = files[0]?.originFileObj; if (!file) return message.warning("请选择项目资料");
    const form = new FormData(); form.append("file", file); setBusy(true);
    try { await api.post("/admin/project-context/sources", form, { headers: { "Content-Type": "multipart/form-data" } }); setFiles([]); message.success("项目资料已上传"); await load(); }
    catch (error: any) { message.error(error.response?.data?.detail || "资料上传失败"); } finally { setBusy(false); }
  };
  const generate = async () => { setBusy(true); try { await api.post("/admin/project-context/summary/generate", undefined, { timeout: 0 }); message.success("项目摘要草稿已生成"); await load(); } catch (error: any) { message.error(error.response?.data?.detail || "摘要生成失败"); } finally { setBusy(false); } };
  const save = async () => { if (!data?.draft) return; setBusy(true); try { await api.put("/admin/project-context/summary/draft", { version_id: data.draft.id, summary_text: draftText }); message.success("草稿已保存"); await load(); } catch (error: any) { message.error(error.response?.data?.detail || "保存失败"); } finally { setBusy(false); } };
  const publish = async () => { if (!data?.draft) return; setBusy(true); try { await api.post("/admin/project-context/summary/publish", { version_id: data.draft.id }); message.success("项目背景已发布"); await load(); } catch (error: any) { message.error(error.response?.data?.detail || "发布失败"); } finally { setBusy(false); } };
  if (!data) return <Spin />;
  return <Space direction="vertical" size={16} style={{ width: "100%" }}>
    <div><Typography.Title level={3} style={{ margin: 0 }}>项目背景</Typography.Title><Typography.Text type="secondary">项目级资料与专栏课件相互独立；只有审核发布的摘要进入学习问答</Typography.Text></div>
    <Card title="项目级资料" extra={<Typography.Text type="secondary">支持 MD / PDF / PPTX</Typography.Text>}>
      <Space style={{ marginBottom: 16 }}><Upload beforeUpload={() => false} maxCount={1} fileList={files} onChange={({ fileList }) => setFiles(fileList.slice(-1))}><Button icon={<UploadOutlined />}>选择资料</Button></Upload><Button type="primary" loading={busy} onClick={upload}>上传资料</Button></Space>
      {data.sources.length ? <Table rowKey="id" dataSource={data.sources} pagination={false} columns={[
        { title: "资料名", dataIndex: "filename" }, { title: "格式", dataIndex: "format", render: (value: string) => <Tag>{value.toUpperCase()}</Tag> },
        { title: "内容指纹", dataIndex: "sha256", render: (value: string) => <Typography.Text code>{value.slice(0, 12)}…</Typography.Text> },
        { title: "操作", render: (_: unknown, row: Source) => <Popconfirm title="确认删除项目资料？" onConfirm={async () => { try { await api.delete(`/admin/project-context/sources/${row.id}`); message.success("资料已删除"); await load(); } catch (error: any) { message.error(error.response?.data?.detail || "删除失败"); } }}><Button danger size="small" icon={<DeleteOutlined />}>删除</Button></Popconfirm> },
      ]} /> : <Empty description="尚未上传项目级资料" />}
    </Card>
    <Card title="已发布项目背景" extra={data.published && <Tag color={data.published.is_stale ? "orange" : "green"}>v{data.published.version}</Tag>}>{data.published ? <Typography.Paragraph style={{ whiteSpace: "pre-wrap" }}>{data.published.summary_text}</Typography.Paragraph> : <Empty description="尚无已发布项目背景" />}</Card>
    <Card title="摘要草稿" extra={<Button loading={busy} onClick={generate}>AI 生成草稿</Button>}>
      {data.draft ? <Space direction="vertical" style={{ width: "100%" }}><Alert type="info" showIcon message={`草稿 v${data.draft.version} 未发布，不会进入问答`} /><Input.TextArea value={draftText} onChange={(event) => setDraftText(event.target.value)} autoSize={{ minRows: 10, maxRows: 24 }} /><Space><Button loading={busy} onClick={save}>保存草稿</Button><Popconfirm title="发布后新会话将使用此版本，确认？" onConfirm={publish}><Button type="primary" loading={busy}>审核通过并发布</Button></Popconfirm></Space></Space> : <Empty description="上传资料后生成摘要草稿" />}
    </Card>
  </Space>;
}
