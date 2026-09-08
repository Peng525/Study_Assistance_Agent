import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Materials from "../pages/admin/Materials";
import { adminMaterials } from "../api/adminMaterials";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn() },
}));

vi.mock("../api/adminMaterials", () => ({
  adminMaterials: {
    listMaterials: vi.fn(),
    getSubtitleStatus: vi.fn(),
    batchGenerateSubtitle: vi.fn(),
    batchCancelSubtitle: vi.fn(),
    batchReview: vi.fn(),
  },
}));

vi.mock("antd", async (importOriginal) => {
  const actual = await importOriginal<any>();
  return {
    ...actual,
    message: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
  };
});

const base = {
  status: "ready",
  courseware_format: "pptx",
  course_type: "theory",
  scanned_at: "2026-09-05T10:30:00",
};

/** 造一行"生成中"的素材。done/total 决定百分比与切片计数。 */
const row = (done: number, total = 45) => ({
  ...base,
  course_id: "c1",
  subtitle_status: "generating",
  subtitle_slices_done: done,
  subtitle_slices_total: total,
});

beforeEach(() => {
  vi.clearAllMocks();
  (api.get as any).mockImplementation((url: string) =>
    Promise.resolve(
      url === "/materials"
        ? { data: [] }
        : { data: { sources: [] } }, // /admin/project-context
    ),
  );
  (adminMaterials.listMaterials as any).mockResolvedValue([]);
  (adminMaterials.getSubtitleStatus as any).mockResolvedValue({
    course_id: "c1",
    subtitle_status: "generating",
    review_state: "unreviewed",
    task_status: "generating",
    task_progress: 0.44,
    task_slices_done: 20,
    task_slices_total: 45,
    task_phase: "transcribing",
    task_started_at: null,
    task_error: null,
    queue_position: 0,
  });
});

describe("素材管理页轮询（PRD AC-15 数据源唯一 / AC-4 进度持续变化）", () => {
  it("进度真的会推进：百分比与切片计数随轮询变化，不是定格在触发瞬间", async () => {
    // ⚠️ 这个用例存在的理由：曾经 `progressMap` 只写不读，渲染吃的是
    // 不会刷新的静态快照，于是百分比定格在触发瞬间 —— 而当时的测试
    // （materialsBatch.test.tsx 里的同名用例）把 35/45 直接塞进首屏 mock
    // 再断言"渲染出 35 / 45"，断言的是渲染函数而不是数据流，所以测不出来。
    const calls = [row(10), row(20), row(30)];
    let n = 0;
    (adminMaterials.listMaterials as any).mockImplementation(() => {
      const data = calls[Math.min(n, calls.length - 1)];
      n += 1;
      return Promise.resolve([data]);
    });
    // 首屏由 load() 走 api.get("/materials")，也得带 generating 行才会启动轮询
    (api.get as any).mockImplementation((url: string) =>
      Promise.resolve(
        url === "/materials"
          ? { data: [row(10)] }
          : { data: { sources: [] } },
      ),
    );

    render(<Materials />);

    // 首次渲染：10/45 = 22%
    expect(await screen.findByText("生成中 22%")).toBeInTheDocument();
    expect(screen.getByText("10 / 45")).toBeInTheDocument();

    // 一个轮询周期（3s）后必须变成 20/45 = 44%
    await waitFor(
      () => {
        expect(screen.getByText("生成中 44%")).toBeInTheDocument();
      },
      { timeout: 8000 },
    );
    expect(screen.getByText("20 / 45")).toBeInTheDocument();
  });

  it("生成期间只轮询列表端点，逐行单查一次都不该发生", async () => {
    (api.get as any).mockImplementation((url: string) =>
      Promise.resolve(
        url === "/materials"
          ? { data: [row(10)] }
          : { data: { sources: [] } },
      ),
    );
    (adminMaterials.listMaterials as any).mockResolvedValue([row(15)]);

    render(<Materials />);
    // 轮询间隔是 3s，waitFor 默认只等 1s —— 必须放大超时才能观察到第二轮
    await waitFor(
      () =>
        expect(
          (adminMaterials.listMaterials as any).mock.calls.length,
        ).toBeGreaterThanOrEqual(2),
      { timeout: 8000 },
    );

    // 单查端点相对列表端点没有增量信息，为一个零增量维护 N 倍请求是纯负债
    expect(adminMaterials.getSubtitleStatus).not.toHaveBeenCalled();
  });

  it("没有生成中的行就完全不轮询", async () => {
    (api.get as any).mockImplementation((url: string) =>
      Promise.resolve(
        url === "/materials"
          ? { data: [{ ...base, course_id: "c1", subtitle_status: "ready", review_state: "unreviewed" }] }
          : { data: { sources: [] } },
      ),
    );
    (adminMaterials.listMaterials as any).mockResolvedValue([
      { ...base, course_id: "c1", subtitle_status: "ready", review_state: "unreviewed" },
    ]);

    render(<Materials />);
    await screen.findByText("c1");
    await new Promise((r) => setTimeout(r, 3400)); // 跨过一个轮询周期

    // 挂载后 load() 只走 api.get，listMaterials 只在轮询里用 —— 不该被调
    expect(adminMaterials.listMaterials).not.toHaveBeenCalled();
    expect(adminMaterials.getSubtitleStatus).not.toHaveBeenCalled();
  });

  it("轮询只更新列表，不退出批量模式、不清选择，也不闪整表 loading", async () => {
    const pending = { ...base, course_id: "c2", subtitle_status: "pending" };
    const active = { ...row(10), subtitle_task_active: true };
    (api.get as any).mockImplementation((url: string) =>
      Promise.resolve(
        url === "/materials"
          ? { data: [active, pending] }
          : { data: { sources: [] } },
      ),
    );
    (adminMaterials.listMaterials as any).mockResolvedValue([row(20), pending]);

    render(<Materials />);
    await screen.findByText("c2");
    fireEvent.click(screen.getByRole("button", { name: /生成字幕$/ }));
    const pendingRow = screen.getByText("c2").closest("tr");
    const checkbox = pendingRow?.querySelector<HTMLInputElement>('input[type="checkbox"]');
    expect(checkbox).toBeTruthy();
    fireEvent.click(checkbox as HTMLInputElement);
    expect(await screen.findByRole("button", { name: /开始生成字幕（1）/ })).toBeInTheDocument();

    await waitFor(
      () => expect(screen.getByText("生成中 44%")).toBeInTheDocument(),
      { timeout: 8000 },
    );
    expect(screen.getByRole("button", { name: /开始生成字幕（1）/ })).toBeInTheDocument();
    expect(checkbox).toBeChecked();
    expect(document.querySelector(".ant-table-wrapper .ant-spin-spinning")).toBeNull();
  });
});
