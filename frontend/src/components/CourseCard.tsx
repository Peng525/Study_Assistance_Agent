import { Link } from "react-router-dom";

export interface CourseCardData {
  course_id: string;
  status: string;
  courseware_format?: string | null;
  subtitle_status?: string;
  title?: string | null;
  display_title?: string | null;
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
  const title = course.display_title?.trim() || "当前视频";
  return (
    <Link
      className="course-card course-card__cover"
      to={`/course/${course.course_id}`}
      title={title}
    >
      <span className="course-card__cover-title" title={title}>{title}</span>
      {course.duration ? <span className="course-card__duration">{formatDuration(course.duration)}</span> : null}
    </Link>
  );
}
