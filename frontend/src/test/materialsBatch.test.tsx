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

const mk = (r: Record<string, unknown>) => ({
  status: "ready",
  courseware_format: "pptx",
  course_type: "theory",
  source_filename: "Spring.pptx",
  scanned_at: "2026-09-05T10:30:00",
  duration: 754,
  ...r,
});

const ROWS = [
  mk({ course_id: "c-pending", subtitle_status: "pending" }),
  mk({ course_id: "c-error", subtitle_status: "error", subtitle_error: "未检测到 ffmpeg" }),
  mk({
    course_id: "c-generating",
    subtitle_status: "generating",
    subtitle_task_active: true,
    subtitle_slices_done: 35,
    subtitle_slices_total: 45,
  }),
  mk({ course_id: "c-ready", subtitle_status: "ready", review_state: "unreviewed", subtitle_filename: "c-ready.vtt", subtitle_has_file: true }),
  mk({ course_id: "c-reviewed", subtitle_status: "ready", review_state: "reviewed", subtitle_filename: "c-reviewed.vtt", subtitle_has_file: true }),
];

beforeEach(() => {
  vi.clearAllMocks();
  (api.get as any).mockImplementation((url: string) =>
    Promise.resolve(
      url === "/materials"
        ? { data: ROWS }
        : { data: { sources: [{ id: 1, filename: "Spring.pptx", column_name: "Spring", format: "pptx" }] } },
    ),
  );
  (adminMaterials.listMaterials as any).mockResolvedValue(ROWS);
  (adminMaterials.batchGenerateSubtitle as any).mockResolvedValue({
    succeeded: 2,
    failed: 0,
    results: [],
  });
  (adminMaterials.batchCancelSubtitle as any).mockResolvedValue({ succeeded: 1, failed: 0, results: [] });
  (adminMaterials.batchReview as any).mockResolvedValue({ succeeded: 1, failed: 0, results: [] });
});

const selectAll = async () => {
  const boxes = await waitFor(() => {
    const found = document.querySelectorAll<HTMLInputElement>('.ant-table-thead input[type="checkbox"]');
    expect(found.length).toBe(1);
    return found;
  });
  fireEvent.click(boxes[0]);
};

describe("动作优先批量操作（PRD AC-7 / AC-19）", () => {
  /** 按课程 ID 勾选单行：不依赖行序号，也不受 rc-table 测量行干扰。 */
  const selectRow = async (courseId: string) => {
    const cell = await screen.findByText(courseId);
    const box = cell.closest("tr")?.querySelector<HTMLInputElement>('input[type="checkbox"]');
    expect(box).toBeTruthy();
    fireEvent.click(box as HTMLInputElement);
    await waitFor(() => expect(box).toBeChecked());
  };

  /** 展开「审核状态」下拉，返回两个菜单项 DOM。 */
  const openReviewMenu = async () => {
    // antd Dropdown 默认 trigger 是 hover，点 click 打不开
    const btn = await screen.findByRole("button", { name: /审核状态/ });
    fireEvent.mouseEnter(btn);
    return waitFor(() => {
      const items = document.querySelectorAll<HTMLElement>(".ant-dropdown-menu-item");
      expect(items.length).toBe(2);
      return items;
    });
  };

  it("默认页常驻三个动作且不显示选择列", async () => {
    render(<Materials />);
    await screen.findByText("c-reviewed");

    expect(screen.getByRole("button", { name: /生成字幕$/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /取消生成$/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /审核状态/ })).toBeInTheDocument();
    expect(document.querySelectorAll('.ant-table-thead input[type="checkbox"]')).toHaveLength(0);
  });

  it("审核入口按全表分别显示标记和撤销数量", async () => {
    render(<Materials />);
    await screen.findByText("c-reviewed");

    const items = await openReviewMenu();
    expect(items[0].textContent).toContain("标记为已审核（1）");
    expect(items[0].className).not.toContain("ant-dropdown-menu-item-disabled");
    expect(items[1].textContent).toContain("撤销已审核（1）");
    expect(items[1].className).not.toContain("ant-dropdown-menu-item-disabled");
  });

  it("标记审核模式只允许未审核行，且只显示当前主操作", async () => {
    render(<Materials />);
    await screen.findByText("c-reviewed");
    const items = await openReviewMenu();
    fireEvent.click(items[0]);
    const reviewedRow = screen.getByText("c-reviewed").closest("tr");
    expect(reviewedRow?.querySelector<HTMLInputElement>('input[type="checkbox"]')).toBeDisabled();
    await selectRow("c-ready");

    expect(screen.getByText("请选择要标记已审核的字幕")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /标记为已审核（1）/ })).not.toBeDisabled();
    expect(screen.queryByRole("button", { name: /撤销已审核/ })).toBeNull();
  });

  it("撤销审核模式只允许已审核行，且只显示当前主操作", async () => {
    render(<Materials />);
    await screen.findByText("c-reviewed");
    const items = await openReviewMenu();
    fireEvent.click(items[1]);
    const unreviewedRow = screen.getByText("c-ready").closest("tr");
    expect(unreviewedRow?.querySelector<HTMLInputElement>('input[type="checkbox"]')).toBeDisabled();
    await selectRow("c-reviewed");

    expect(screen.getByText("请选择要撤销审核的字幕")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /撤销已审核（1）/ })).not.toBeDisabled();
    expect(screen.queryByRole("button", { name: /标记为已审核/ })).toBeNull();
  });
});

describe("素材管理页批量操作（PRD v8 §5.5A.5）", () => {
  it("表头不再有「操作」列，单行操作收进「更多」下拉", async () => {
    render(<Materials />);
    expect(await screen.findByText("c-pending")).toBeInTheDocument();
    expect(screen.queryByText("操作")).not.toBeInTheDocument();
    // 「更多」既出现在表头，也出现在每一行的下拉触发器上
    expect(screen.getAllByText("更多").length).toBeGreaterThan(1);
  });

  // ⚠️ 本用例只验证"渲染函数把给定的进度渲染出来了"，**不验证进度会推进**。
  // 它把 35/45 直接塞进首屏 mock，所以即使轮询完全失效、百分比定格在触发瞬间，
  // 它照样是绿的（v8 时就是这样漏掉了 AC-4 不通过）。
  // "进度真的在动"由 materialsPolling.test.tsx 断言 —— 那里让首屏与轮询返回不同的值。
  it("生成中的行按给定进度渲染出百分比与切片计数", async () => {
    render(<Materials />);
    expect(await screen.findByText("生成中 78%")).toBeInTheDocument();
    expect(screen.getByText("35 / 45")).toBeInTheDocument();
  });

  it("点生成字幕后才出现选择列，N=0 时确认按钮禁用", async () => {
    render(<Materials />);
    await screen.findByText("c-pending");
    fireEvent.click(screen.getByRole("button", { name: /生成字幕$/ }));

    expect(await screen.findByRole("button", { name: /开始生成字幕（0）/ })).toBeDisabled();
    expect(document.querySelectorAll('.ant-table-thead input[type="checkbox"]')).toHaveLength(1);
  });

  it("选择后再确认，只提交 pending + error 的 ID", async () => {
    render(<Materials />);
    await screen.findByText("c-pending");
    fireEvent.click(screen.getByRole("button", { name: /生成字幕$/ }));
    await selectAll();

    fireEvent.click(await screen.findByRole("button", { name: /开始生成字幕（2）/ }));

    await waitFor(() =>
      expect(adminMaterials.batchGenerateSubtitle).toHaveBeenCalledWith(["c-pending", "c-error"]),
    );
  });

  it("批量操作部分失败时保留失败项和当前模式以便重试", async () => {
    (adminMaterials.batchGenerateSubtitle as any).mockResolvedValueOnce({
      succeeded: 1,
      failed: 1,
      results: [
        { course_id: "c-pending", ok: true },
        { course_id: "c-error", ok: false, error: "任务启动失败" },
      ],
    });
    render(<Materials />);
    await screen.findByText("c-pending");
    fireEvent.click(screen.getByRole("button", { name: /生成字幕$/ }));
    await selectAll();
    fireEvent.click(await screen.findByRole("button", { name: /开始生成字幕（2）/ }));

    const retryButton = await screen.findByRole("button", { name: /开始生成字幕（1）/ });
    expect(retryButton).toBeEnabled();
    expect(screen.getByText("请选择要生成字幕的素材")).toBeInTheDocument();
    const succeededBox = screen.getByText("c-pending").closest("tr")
      ?.querySelector<HTMLInputElement>('input[type="checkbox"]');
    const failedBox = screen.getByText("c-error").closest("tr")
      ?.querySelector<HTMLInputElement>('input[type="checkbox"]');
    expect(succeededBox).not.toBeChecked();
    expect(failedBox).toBeChecked();
  });

  it("退出选择模式不发 API，并恢复默认工具栏", async () => {
    render(<Materials />);
    await screen.findByText("c-pending");
    fireEvent.click(screen.getByRole("button", { name: /生成字幕$/ }));
    await selectAll();
    expect(screen.queryByText(/已选择 \d+ 项/)).toBeNull();
    expect(screen.queryByRole("button", { name: "清空选择" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "取消选择" }));

    expect(adminMaterials.batchGenerateSubtitle).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /生成字幕$/ })).toBeInTheDocument();
    expect(document.querySelectorAll('.ant-table-thead input[type="checkbox"]')).toHaveLength(0);
  });

  it("已 ready 的行给出「查看字幕」入口，未生成的行不画假图标", async () => {
    render(<Materials />);
    expect(await screen.findByText("c-pending")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /查看字幕/ }).length).toBe(2);
  });
});
