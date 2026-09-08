import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Materials from "../pages/admin/Materials";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn() },
}));

vi.mock("antd", async (importOriginal) => {
  const actual = await importOriginal<any>();
  return {
    ...actual,
    message: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
  };
});

beforeEach(() => {
  vi.clearAllMocks();
  (api.get as any).mockImplementation((url: string) => Promise.resolve(
    url === "/materials"
      ? { data: [] }
      : { data: { sources: [{ id: 1, filename: "Spring.pptx", column_name: "Spring", format: "pptx" }] } },
  ));
});

describe("素材上传专栏归类", () => {
  it("上传视频时必须选择专栏，课程类型只用于管理分类", async () => {
    render(<Materials />);
    fireEvent.click(await screen.findByRole("button", { name: /上传文件/ }));

    expect(await screen.findByLabelText("所属专栏")).toBeInTheDocument();
    expect(screen.getByText(/视频上传后会直接归入所选 PPT 专栏/)).toBeInTheDocument();
    expect(screen.getByText(/理论和实战都会使用专栏总大纲与当前视频课件原文/)).toBeInTheDocument();
  });

  it("选择文件后用文件名预填课程标识，但不覆盖用户输入", async () => {
    render(<Materials />);
    fireEvent.click(await screen.findByRole("button", { name: /上传文件/ }));
    const courseId = await screen.findByLabelText("课程标识");
    const fileInput = document.querySelector<HTMLInputElement>('input[type="file"]');
    expect(fileInput).toBeTruthy();

    fireEvent.change(fileInput as HTMLInputElement, {
      target: { files: [new File(["video"], "004.Spring - 容器和组件.mp4", { type: "video/mp4" })] },
    });
    await waitFor(() => expect(courseId).toHaveValue("004.Spring - 容器和组件"));

    fireEvent.change(courseId, { target: { value: "我修改后的课程标识" } });
    fireEvent.change(fileInput as HTMLInputElement, {
      target: { files: [new File(["video2"], "005.Spring.mp4", { type: "video/mp4" })] },
    });
    await waitFor(() => expect(courseId).toHaveValue("我修改后的课程标识"));
  });
});
