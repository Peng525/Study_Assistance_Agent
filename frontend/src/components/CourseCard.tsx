import { Badge, Card, Tag } from "antd";
import { useNavigate } from "react-router-dom";

export interface CourseCardData {
  course_id: string;
  status: string;
  courseware_format?: string | null;
  subtitle_status?: string;
  title?: string;
  description?: string;
}

export default function CourseCard({ course }: { course: CourseCardData }) {
  const navigate = useNavigate();
  return (
    <Card
      hoverable
      style={{ width: 280 }}
      cover={
        <div
          style={{
            height: 140,
            background: "linear-gradient(135deg, #1677ff 0%, #4096ff 100%)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "#fff",
            fontSize: 28,
            fontWeight: 700,
          }}
        >
          {course.title || course.course_id}
        </div>
      }
      onClick={() => navigate(`/course/${course.course_id}`)}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <strong>{course.title || course.course_id}</strong>
        <Badge status="success" text="Ready" />
      </div>
      <div style={{ marginTop: 8, color: "var(--text-secondary)", fontSize: 12 }}>
        {course.description || "课程描述"}
      </div>
      <div style={{ marginTop: 8 }}>
        <Tag>{course.course_id}</Tag>
        {course.courseware_format && <Tag color="blue">{course.courseware_format}</Tag>}
      </div>
    </Card>
  );
}
