import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Materials from "../pages/admin/Materials";
import { api } from "../api/client";

vi.mock("../api/client", () => ({ api: { get: vi.fn(), post: vi.fn(), put: vi.fn() } }));
vi.mock("antd", async (importOriginal) => ({ ...(await importOriginal<any>()), message: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

beforeEach(() => {
  vi.clearAllMocks();
  (api.get as any).mockImplementation((url: string) => Promise.resolve(url === "/materials" ? { data: [] } : { data: [{ id: 1, name: "Spring" }] }));
});

describe("专栏驱动的视频上传", () => {
  it("专栏内选择视频后预填课程标识且不显示所属专栏", async () => {
    render(<Materials seriesId={1} />);
    fireEvent.click(await screen.findByRole("button", { name: /上传视频/ }));
    expect(screen.queryByLabelText("所属专栏")).not.toBeInTheDocument();
    const courseId = screen.getByLabelText("课程标识");
    const fileInput = document.querySelector<HTMLInputElement>('input[type="file"]')!;
    fireEvent.change(fileInput, { target: { files: [new File(["video"], "004.Spring - 容器和组件.mp4", { type: "video/mp4" })] } });
    await waitFor(() => expect(courseId).toHaveValue("004.Spring - 容器和组件"));
    fireEvent.change(courseId, { target: { value: "自定义标识" } });
    fireEvent.change(fileInput, { target: { files: [new File(["video"], "005.Spring.mp4")] } });
    await waitFor(() => expect(courseId).toHaveValue("自定义标识"));
  });
});
