import { Badge, Card, Tag } from "antd";
import { useNavigate } from "react-router-dom";

export interface CourseCardData {
  course_id: string;
  status: string;
  courseware_format?: string | null;
  subtitle_status?: string;
  title?: string | null;
  description?: string;
  duration?: number | null;
}

function formatDuration(seconds?: number | null): string {
  if (!seconds) return "";
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  return `${m}:${String(sec).padStart(2, "0")}`;
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
            position: "relative",
          }}
        >
          {course.title || course.course_id}
          {course.duration ? (
            <span
              style={{
                position: "absolute",
                bottom: 8,
                right: 8,
                fontSize: 12,
                background: "rgba(0,0,0,0.6)",
                padding: "2px 6px",
                borderRadius: 4,
              }}
            >
              {formatDuration(course.duration)}
            </span>
          ) : null}
        </div>
      }
      onClick={() => navigate(`/course/${course.course_id}`)}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <strong>{course.title || course.course_id}</strong>
        <Badge status="success" text="Ready" />
      </div>
      <div style={{ marginTop: 8, color: "var(--text-secondary)", fontSize: 12 }}>
        {course.description || course.course_id}
      </div>
      <div style={{ marginTop: 8 }}>
        <Tag>{course.course_id}</Tag>
        {course.courseware_format && <Tag color="blue">{course.courseware_format}</Tag>}
      </div>
    </Card>
  );
}
