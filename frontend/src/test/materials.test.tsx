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
  (api.get as any).mockResolvedValue({ data: [] });
});

describe("素材上传课程类型", () => {
  it("上传视频时默认选择理论通用且说明不生成大纲", async () => {
    render(<Materials />);
    fireEvent.click(await screen.findByRole("button", { name: /上传文件/ }));

    expect(await screen.findByText("理论/通用（不生成大纲）")).toBeInTheDocument();
    expect(screen.getByText(/理论\/通用课程默认不生成也不向 AI 发送大纲/)).toBeInTheDocument();
  });
});
