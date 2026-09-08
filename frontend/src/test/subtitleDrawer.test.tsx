import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { message } from "antd";
import SubtitleDrawer, { SubtitleDrawerRow } from "../components/SubtitleDrawer";
import { adminMaterials } from "../api/adminMaterials";

vi.mock("../api/adminMaterials", () => ({
  adminMaterials: {
    getCues: vi.fn(),
    putCues: vi.fn(),
  },
}));

vi.mock("antd", async (importOriginal) => {
  const actual = await importOriginal<any>();
  return {
    ...actual,
    message: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
  };
});


const ROW: SubtitleDrawerRow = {
  course_id: "spring-intro-003",
  subtitle_status: "ready",
  review_state: "unreviewed",
  subtitle_source: "whisper",
  subtitle_filename: "spring-intro-003.vtt",
  subtitle_relative_path: "materials/spring-intro-003/spring-intro-003.vtt",
  scanned_at: "2026-09-05T10:30:00",
  duration: 754,
};

const CUES = [
  { start: 0, end: 2.5, text: "第一条字幕" },
  { start: 2.5, end: 5, text: "第二条字幕" },
];

const noop = () => {};

function renderDrawer(overrides: Partial<SubtitleDrawerRow> = {}, open = true) {
  const props = {
    open,
    row: { ...ROW, ...overrides },
    onClose: vi.fn(),
    onSaved: vi.fn(),
    onRegenerate: vi.fn(),
    onReviewToggle: vi.fn(),
  };
  render(<SubtitleDrawer {...props} />);
  return props;
}

beforeEach(() => {
  vi.clearAllMocks();
  (adminMaterials.getCues as any).mockResolvedValue({ cues: CUES, revision: "abc12345" });
});

describe("字幕详情 Drawer（PRD v8 §5.5A.6）", () => {
  it("打开即加载字幕，条数取解析后的 cue 数而不是文件行数", async () => {
    renderDrawer();
    expect(await screen.findByText("第一条字幕")).toBeInTheDocument();
    expect(screen.getByText(/2 条/)).toBeInTheDocument();
    expect(adminMaterials.getCues).toHaveBeenCalledWith("spring-intro-003");
  });

  it("元数据照抄后端返回，不自行拼接存储路径，也不把扫描时间当生成时间", async () => {
    renderDrawer();
    await screen.findByText("第一条字幕");
    expect(screen.getByText("materials/spring-intro-003/spring-intro-003.vtt")).toBeInTheDocument();
    expect(screen.getByText("最近扫描")).toBeInTheDocument();
    expect(screen.queryByText("生成时间")).not.toBeInTheDocument();
    expect(screen.getByText("AI 自动生成 (Whisper)")).toBeInTheDocument();
  });

  it("查看 → 编辑 → 保存：同一层切换，不弹新窗口，保存后回查看态并通知父级刷新", async () => {
    const props = renderDrawer();
    await screen.findByText("第一条字幕");

    fireEvent.click(screen.getByRole("button", { name: /编辑字幕/ }));
    const boxes = await waitFor(() => {
      const found = document.querySelectorAll(".ant-table textarea");
      expect(found.length).toBe(2);
      return found;
    });

    fireEvent.change(boxes[0], { target: { value: "改过的一条" } });
    (adminMaterials.putCues as any).mockResolvedValue({ revision: "def67890" });

    fireEvent.click(screen.getByRole("button", { name: /保存修改/ }));

    await waitFor(() => {
      expect(adminMaterials.putCues).toHaveBeenCalledWith(
        "spring-intro-003",
        [
          { start: 0, end: 2.5, text: "改过的一条" },
          { start: 2.5, end: 5, text: "第二条字幕" },
        ],
        "abc12345",
      );
    });
    await waitFor(() => expect(props.onSaved).toHaveBeenCalled());
    // 保存成功回到查看态
    expect(await screen.findByRole("button", { name: /编辑字幕/ })).toBeInTheDocument();
  });

  it("时间轴非法时不提交，先报错", async () => {
    (adminMaterials.getCues as any).mockResolvedValue({
      cues: [{ start: 5, end: 3, text: "时间倒挂" }],
      revision: "abc12345",
    });
    renderDrawer();
    await screen.findByText("时间倒挂");

    fireEvent.click(screen.getByRole("button", { name: /编辑字幕/ }));
    await waitFor(() => expect(document.querySelectorAll(".ant-table textarea").length).toBe(1));
    fireEvent.click(screen.getByRole("button", { name: /保存修改/ }));

    await waitFor(() => expect(message.error).toHaveBeenCalled());
    expect(adminMaterials.putCues).not.toHaveBeenCalled();
  });

  it("保存撞上 409 时自动重拉最新内容，不静默丢弃用户编辑", async () => {
    renderDrawer();
    await screen.findByText("第一条字幕");
    fireEvent.click(screen.getByRole("button", { name: /编辑字幕/ }));
    await waitFor(() => expect(document.querySelectorAll(".ant-table textarea").length).toBe(2));

    const conflict: any = new Error("conflict");
    conflict.response = { status: 409 };
    (adminMaterials.putCues as any).mockRejectedValueOnce(conflict);
    (adminMaterials.getCues as any).mockResolvedValueOnce({
      cues: [{ start: 0, end: 2.5, text: "别人改过的版本" }],
      revision: "zzz99999",
    });

    fireEvent.click(screen.getByRole("button", { name: /保存修改/ }));
    await waitFor(() => expect(message.warning).toHaveBeenCalled());
    expect(adminMaterials.getCues).toHaveBeenCalledTimes(2); // 打开一次 + 冲突重拉一次
  });

  it("未生成完成时不加载内容，只给状态提示与重新生成入口", async () => {
    renderDrawer({ subtitle_status: "generating", subtitle_filename: null });
    expect(await screen.findByText(/暂无字幕内容可查看/)).toBeInTheDocument();
    expect(adminMaterials.getCues).not.toHaveBeenCalled();
  });

  it("已审核的行显示「撤销审核」，未审核显示「标记已审核」", async () => {
    const reviewed = renderDrawer({ review_state: "reviewed" });
    await screen.findByText("第一条字幕");
    fireEvent.click(screen.getByRole("button", { name: /撤销审核/ }));
    expect(reviewed.onReviewToggle).not.toHaveBeenCalled();
    const confirm = await waitFor(() => {
      const node = document.querySelector<HTMLButtonElement>(
        ".ant-popconfirm-buttons .ant-btn-primary",
      );
      expect(node).toBeTruthy();
      return node as HTMLButtonElement;
    });
    fireEvent.click(confirm);
    await waitFor(() =>
      expect(reviewed.onReviewToggle).toHaveBeenCalledWith(
        expect.objectContaining({ course_id: "spring-intro-003" }),
      ),
    );
  });

  it("可在指定 cue 后插入，并按前后时间轴生成非零区间", async () => {
    (adminMaterials.getCues as any).mockResolvedValue({
      cues: [
        { start: 0, end: 2, text: "前一条" },
        { start: 5, end: 7, text: "后一条" },
      ],
      revision: "abc12345",
    });
    (adminMaterials.putCues as any).mockResolvedValue({ revision: "next1234" });
    renderDrawer();
    await screen.findByText("前一条");
    fireEvent.click(screen.getByRole("button", { name: /编辑字幕/ }));
    const insertButtons = await screen.findAllByTitle("在此条后插入");
    fireEvent.click(insertButtons[0]);

    const boxes = await waitFor(() => {
      const found = document.querySelectorAll<HTMLTextAreaElement>(".ant-table textarea");
      expect(found.length).toBe(3);
      return found;
    });
    fireEvent.change(boxes[1], { target: { value: "插入的一条" } });
    fireEvent.click(screen.getByRole("button", { name: /保存修改/ }));

    await waitFor(() =>
      expect(adminMaterials.putCues).toHaveBeenCalledWith(
        "spring-intro-003",
        [
          { start: 0, end: 2, text: "前一条" },
          { start: 2, end: 4, text: "插入的一条" },
          { start: 5, end: 7, text: "后一条" },
        ],
        "abc12345",
      ),
    );
  });

  it("相邻 cue 没有时间空隙时拒绝插入零时长字幕", async () => {
    (adminMaterials.getCues as any).mockResolvedValue({
      cues: [
        { start: 0, end: 2, text: "前一条" },
        { start: 2, end: 4, text: "后一条" },
      ],
      revision: "abc12345",
    });
    renderDrawer();
    await screen.findByText("前一条");
    fireEvent.click(screen.getByRole("button", { name: /编辑字幕/ }));
    const insertButtons = await screen.findAllByTitle("在此条后插入");
    fireEvent.click(insertButtons[0]);

    expect(document.querySelectorAll(".ant-table textarea")).toHaveLength(2);
    expect(message.warning).toHaveBeenCalledWith(
      "相邻字幕之间没有可插入的时间，请先调整时间轴",
    );
  });
});
