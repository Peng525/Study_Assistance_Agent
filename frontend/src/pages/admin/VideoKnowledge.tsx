import { useEffect, useState } from "react";
import { Alert, Button, Card, Empty, Input, InputNumber, Select, Space, Spin, Typography, message } from "antd";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../../api/client";

interface Page { page: number; text: string; }
interface Series { id: number; name: string; source: { id: number } | null; }
interface Video { course_id: string; video_name: string; series_id: number | null; course_type: "theory" | "practice"; page_start: number | null; page_end: number | null; knowledge_text: string; }

export default function VideoKnowledge() {
  const navigate = useNavigate();
  const { seriesId, courseId = "" } = useParams();
  const id = Number(seriesId);
  const decodedCourseId = decodeURIComponent(courseId);
  const [series, setSeries] = useState<Series | null>(null);
  const [video, setVideo] = useState<Video | null>(null);
  const [pages, setPages] = useState<Page[]>([]);
  const [start, setStart] = useState<number | null>(null);
  const [end, setEnd] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const load = async () => {
    setLoading(true);
    try {
      const [columns, context] = await Promise.all([api.get<Series[]>("/admin/columns"), api.get("/admin/project-context")]);
      const nextSeries = columns.data.find((item) => item.id === id) || null;
      const nextVideo = (context.data.videos || []).find((item: Video) => item.course_id === decodedCourseId && item.series_id === id) || null;
      setSeries(nextSeries); setVideo(nextVideo); setStart(nextVideo?.page_start || null); setEnd(nextVideo?.page_end || null);
      if (nextSeries?.source) setPages((await api.get(`/admin/project-context/sources/${nextSeries.source.id}/pages`)).data.pages || []);
      else setPages([]);
    } catch (error: any) { message.error(error.response?.data?.detail || "课程知识加载失败"); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, [id, decodedCourseId]);
  if (loading) return <Spin />;
  if (!series || !video) return <Empty description="视频不存在或不属于当前专栏" />;
  const preview = pages.filter((page) => start != null && end != null && page.page >= start && page.page <= end).map((page) => page.text).join("\n\n");
  const save = async () => {
    if (!series.source || !start || !end) return;
    setBusy(true);
    try {
      await api.put(`/admin/project-context/videos/${encodeURIComponent(video.course_id)}/knowledge`, { source_id: series.source.id, page_start: start, page_end: end, course_type: video.course_type });
      message.success("课程文本已生成"); await load();
    } catch (error: any) { message.error(error.response?.data?.detail || "课程文本生成失败"); }
    finally { setBusy(false); }
  };
  const updateCourseType = async (courseType: Video["course_type"]) => {
    try {
      await api.put(`/admin/project-context/videos/${encodeURIComponent(video.course_id)}/course-type`, { course_type: courseType });
      await load();
    } catch (error: any) {
      message.error(error.response?.data?.detail || "课程类型保存失败");
    }
  };
  return <Space direction="vertical" size={16} style={{ width: "100%" }}>
    <Button type="link" style={{ padding: 0, width: "fit-content" }} onClick={() => navigate(`/admin/columns/${series.id}`)}>← 返回 {series.name}</Button>
    <Typography.Title level={3} style={{ margin: 0 }}>{video.video_name} · 课程知识</Typography.Title>
    {!series.source && <Alert type="warning" showIcon message="专栏尚无 PPT；视频和字幕可正常使用，课程文本暂不可配置。" />}
    <Card><Space wrap><Typography.Text strong>课程类型</Typography.Text><Select aria-label="课程类型" value={video.course_type} style={{ width: 140 }} onChange={updateCourseType} options={[{ value: "theory", label: "理论/通用" }, { value: "practice", label: "实战/案例" }]} />{series.source && <><Typography.Text strong>PPT 页码</Typography.Text><InputNumber min={1} max={pages.length || 1} value={start} onChange={setStart} /><Typography.Text>至</Typography.Text><InputNumber min={1} max={pages.length || 1} value={end} onChange={setEnd} /><Button type="primary" loading={busy} onClick={save}>生成课程文本</Button></>}</Space></Card>
    <Card title="当前视频 PPT 页原文"><Input.TextArea readOnly value={preview || video.knowledge_text} autoSize={{ minRows: 18, maxRows: 30 }} placeholder="请选择页区间并生成课程文本" /></Card>
  </Space>;
}
