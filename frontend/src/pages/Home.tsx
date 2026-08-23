import { useEffect, useState } from "react";
import { Button, Card, Col, Progress, Row, Skeleton, Tabs, Typography } from "antd";
import { PlayCircleOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import TopNav from "../components/TopNav";
import CourseCard, { CourseCardData } from "../components/CourseCard";
import { api } from "../api/client";
import { latestProgress, loadAll, loadProgress } from "../store/progress";

export default function Home() {
  const [courses, setCourses] = useState<CourseCardData[]>([]);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();
  const latest = latestProgress();

  useEffect(() => {
    api
      .get("/materials")
      .then((r) => setCourses(r.data))
      .finally(() => setLoading(false));
  }, []);

  const videoCourses = courses; // Phase 0 全部为视频课程
  const continueCourse = latest
    ? courses.find((c) => c.course_id === latest.courseId) || courses[0]
    : courses[0];
  const continuePct =
    latest && continueCourse?.duration
      ? Math.min(100, Math.round((latest.time / continueCourse.duration) * 100))
      : 0;
  // 学习轨迹：有进度记录的课程
  const progressRecords = loadAll();
  const learnedCourses = courses.filter((c) => progressRecords[c.course_id]);

  return (
    <div style={{ minHeight: "100%", background: "var(--bg)" }}>
      <TopNav />
      <div style={{ padding: 24, maxWidth: 1200, margin: "0 auto" }}>
        {/* Hero 横幅 */}
        <Card
          style={{
            background: "linear-gradient(135deg, #1677ff 0%, #69b1ff 100%)",
            color: "#fff",
            border: "none",
            marginBottom: 24,
          }}
        >
          <Row align="middle" justify="space-between">
            <Col>
              <Typography.Title level={2} style={{ color: "#fff", marginBottom: 8 }}>
                继续你的学习之旅
              </Typography.Title>
              <Typography.Text style={{ color: "rgba(255,255,255,0.85)" }}>
                {latest
                  ? `上次学习：${latest.courseId}（已学 ${Math.floor(latest.time / 60)} 分钟）`
                  : "选择一门课程开始学习"}
              </Typography.Text>
            </Col>
            <Col style={{ textAlign: "center" }}>
              <Button
                size="large"
                icon={<PlayCircleOutlined />}
                onClick={() => continueCourse && navigate(`/course/${continueCourse.course_id}`)}
                disabled={!continueCourse}
              >
                继续学习
              </Button>
              {latest && continueCourse?.duration ? (
                <div style={{ fontSize: 12, marginTop: 8 }}>
                  进度 {continuePct}%
                </div>
              ) : null}
            </Col>
          </Row>
        </Card>

        {/* 今天学点什么 推荐区 */}
        <Typography.Title level={4}>今天学点什么</Typography.Title>
        <Tabs
          defaultActiveKey="all"
          items={[
            { key: "all", label: "全部" },
            { key: "video", label: "视频" },
            { key: "column", label: "专栏" },
          ]}
        />
        {loading ? (
          <Skeleton active />
        ) : (
          <Row gutter={[16, 16]}>
            {videoCourses.map((c) => (
              <Col key={c.course_id}>
                <CourseCard course={c} />
              </Col>
            ))}
          </Row>
        )}

        {/* 学习轨迹 */}
        <Card style={{ marginTop: 24 }} title="学习轨迹">
          {learnedCourses.length === 0 ? (
            <Typography.Text type="secondary">还没有学习记录，开始第一门课程吧</Typography.Text>
          ) : (
            learnedCourses.map((c) => {
              const rec = loadProgress(c.course_id)!;
              const pct = c.duration
                ? Math.min(100, Math.round((rec.time / c.duration) * 100))
                : 0;
              return (
                <Row
                  key={c.course_id}
                  align="middle"
                  style={{ padding: "8px 0", borderBottom: "1px solid var(--border)" }}
                >
                  <Col span={6}>
                    <a onClick={() => navigate(`/course/${c.course_id}`)}>{c.title || c.course_id}</a>
                  </Col>
                  <Col span={16}>
                    <Progress percent={pct} size="small" />
                  </Col>
                  <Col span={2} style={{ textAlign: "right", fontSize: 12 }}>
                    {Math.floor(rec.time / 60)} 分钟
                  </Col>
                </Row>
              );
            })
          )}
        </Card>
      </div>
    </div>
  );
}
