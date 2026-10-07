import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { expect, it } from "vitest";
import CourseCard from "../components/CourseCard";

it("只显示完整标题和时长，不显示内部元数据或重复标题", () => {
  const title = "006.容器注册实验 @Bean " + "VeryLongTechnicalIdentifier".repeat(8);
  const id = "course-" + "x".repeat(180);
  const { container } = render(<MemoryRouter><CourseCard course={{
    course_id: id, status: "ready", display_title: title, title: "旧课件标题", description: "辅助说明", courseware_format: "pptx", duration: 151,
  }} /></MemoryRouter>);
  expect(container.querySelector(".course-card__cover-title")).toHaveAttribute("title", title);
  expect(screen.getAllByText(title)).toHaveLength(1);
  expect(screen.getByRole("link")).toHaveAttribute("href", `/course/${id}`);
  expect(screen.getByText("2:31")).toBeInTheDocument();
  for (const text of [id, "Ready", "pptx", "辅助说明", "旧课件标题"]) expect(screen.queryByText(text)).toBeNull();
});

it("缺失展示名称时不拿课程标识冒充标题", () => {
  render(<MemoryRouter><CourseCard course={{ course_id: "internal-id", status: "ready", title: " " }} /></MemoryRouter>);
  expect(screen.getByText("当前视频")).toBeInTheDocument();
  expect(screen.queryByText("internal-id")).toBeNull();
});
