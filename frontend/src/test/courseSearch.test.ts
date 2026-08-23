import { describe, it, expect } from "vitest";
import { CourseCardData } from "../components/CourseCard";

// 课程搜索过滤逻辑（从 CourseList 抽出的纯函数）
export function filterCourses(courses: CourseCardData[], keyword: string): CourseCardData[] {
  const kw = keyword.trim().toLowerCase();
  if (!kw) return courses;
  return courses.filter(
    (c) =>
      c.course_id.toLowerCase().includes(kw) ||
      (c.title || "").toLowerCase().includes(kw) ||
      (c.description || "").toLowerCase().includes(kw),
  );
}

describe("课程搜索过滤", () => {
  const courses: CourseCardData[] = [
    { course_id: "rag-001", status: "ready", title: "RAG 检索增强", description: "讲 RAG" },
    { course_id: "llm-002", status: "ready", title: "LLM 应用", description: "讲大模型" },
  ];

  it("空关键词返回全部", () => {
    expect(filterCourses(courses, "")).toHaveLength(2);
  });

  it("按 course_id 匹配", () => {
    expect(filterCourses(courses, "rag")).toHaveLength(1);
    expect(filterCourses(courses, "rag")[0].course_id).toBe("rag-001");
  });

  it("按标题匹配", () => {
    expect(filterCourses(courses, "大模型")).toHaveLength(1);
    expect(filterCourses(courses, "大模型")[0].course_id).toBe("llm-002");
  });

  it("无匹配返回空", () => {
    expect(filterCourses(courses, "不存在")).toHaveLength(0);
  });
});
