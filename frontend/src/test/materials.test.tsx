import { fireEvent, render, screen } from "@testing-library/react";
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
});
