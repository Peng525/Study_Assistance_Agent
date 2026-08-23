import { useEffect, useState } from "react";
import { Button, Card, Col, Row, Skeleton, Tabs, Typography } from "antd";
import { PlayCircleOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import TopNav from "../components/TopNav";
import CourseCard, { CourseCardData } from "../components/CourseCard";
import { api } from "../api/client";

export default function Home() {
  const [courses, setCourses] = useState<CourseCardData[]>([]);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  useEffect(() => {
    api
      .get("/materials")
      .then((r) => setCourses(r.data))
      .finally(() => setLoading(false));
  }, []);

  const videoCourses = courses; // Phase 0 全部为视频课程

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
                上次学习进度已保存，从上次的位置继续
              </Typography.Text>
            </Col>
            <Col>
              <Button
                size="large"
                icon={<PlayCircleOutlined />}
                onClick={() => courses[0] && navigate(`/course/${courses[0].course_id}`)}
              >
                继续学习
              </Button>
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
          <Typography.Text type="secondary">
            本周学习时长统计与继续学习入口（Phase 0 占位）
          </Typography.Text>
        </Card>
      </div>
    </div>
  );
}
