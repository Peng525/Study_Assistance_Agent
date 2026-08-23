import { useEffect, useMemo, useState } from "react";
import { Col, Empty, Input, Row, Skeleton } from "antd";
import { useSearchParams } from "react-router-dom";
import TopNav from "../components/TopNav";
import CourseCard, { CourseCardData } from "../components/CourseCard";
import { api } from "../api/client";

export default function CourseList() {
  const [courses, setCourses] = useState<CourseCardData[]>([]);
  const [loading, setLoading] = useState(true);
  const [keyword, setKeyword] = useState("");
  const [searchParams] = useSearchParams();

  useEffect(() => {
    api
      .get("/materials")
      .then((r) => setCourses(r.data))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const q = searchParams.get("q");
    if (q) setKeyword(q);
  }, [searchParams]);

  const filtered = useMemo(() => {
    const kw = keyword.trim().toLowerCase();
    if (!kw) return courses;
    return courses.filter(
      (c) =>
        c.course_id.toLowerCase().includes(kw) ||
        (c.title || "").toLowerCase().includes(kw) ||
        (c.description || "").toLowerCase().includes(kw),
    );
  }, [courses, keyword]);

  return (
    <div style={{ minHeight: "100%", background: "var(--bg)" }}>
      <TopNav />
      <div style={{ padding: 24, maxWidth: 1200, margin: "0 auto" }}>
        <Input.Search
          placeholder="搜索课程标题 / 描述 / course_id"
          allowClear
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          style={{ maxWidth: 400, marginBottom: 24 }}
        />
        {loading ? (
          <Skeleton active />
        ) : filtered.length === 0 ? (
          <Empty description="暂无匹配课程" />
        ) : (
          <Row gutter={[16, 16]}>
            {filtered.map((c) => (
              <Col key={c.course_id}>
                <CourseCard course={c} />
              </Col>
            ))}
          </Row>
        )}
      </div>
    </div>
  );
}
