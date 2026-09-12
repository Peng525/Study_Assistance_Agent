import { describe, expect, it } from "vitest";
import {
  canSelect,
  deriveSubtitleState,
  formatElapsed,
  pickBatchIds,
  type SubtitleRuntimeFields,
} from "../utils/subtitleStatus";

/** 造一行最小字段集；未传的字段走 undefined，等价于后端没给。 */
const row = (r: SubtitleRuntimeFields & { course_id?: string } = {}) => ({
  course_id: "c1",
  ...r,
});

describe("字幕展示态派生（PRD v8 §5.5A.3 状态机）", () => {
  it("后端 4 个真值各自落到对应展示态", () => {
    expect(deriveSubtitleState(row()).kind).toBe("pending");
    expect(deriveSubtitleState(row({ subtitle_status: "generating" })).kind).toBe("transcribing");
    expect(deriveSubtitleState(row({ subtitle_status: "ready" })).kind).toBe("unreviewed");
    expect(deriveSubtitleState(row({ subtitle_status: "error" })).kind).toBe("error");
  });

  it("ready 的主展示统一为已生成，校对状态只保留为次级 kind", () => {
    expect(deriveSubtitleState(row({ subtitle_status: "ready", review_state: "reviewed" }))).toMatchObject(
      { kind: "reviewed", label: "已生成" },
    );
    expect(deriveSubtitleState(row({ subtitle_status: "ready", review_state: "unreviewed" }))).toMatchObject(
      { kind: "unreviewed", label: "已生成" },
    );
    expect(deriveSubtitleState(row({ subtitle_status: "ready" }))).toMatchObject(
      { kind: "unreviewed", label: "已生成" },
    );
  });

  it("还在排队时报排队位次，不报 0% 进度", () => {
    const st = deriveSubtitleState(
      row({ subtitle_status: "generating", subtitle_queue_position: 3 }),
    );
    expect(st.kind).toBe("queued");
    expect(st.queuePosition).toBe(3);
    expect(st.label).toContain("第 3 位");
    // 排队位次优先于切片进度：即使切片数据齐全也不该显示百分比
    expect(st.percent).toBeUndefined();
  });

  it("有切片总数 → 百分比 + 切片计数，且百分比封顶 100", () => {
    const st = deriveSubtitleState(
      row({
        subtitle_status: "generating",
        subtitle_slices_done: 35,
        subtitle_slices_total: 45,
      }),
    );
    expect(st.kind).toBe("transcribing");
    expect(st.percent).toBe(78);
    expect(st.slicesDone).toBe(35);
    expect(st.slicesTotal).toBe(45);

    const overcapped = deriveSubtitleState(
      row({ subtitle_status: "generating", subtitle_slices_done: 99, subtitle_slices_total: 45 }),
    );
    expect(overcapped.percent).toBe(100);
  });

  it("合并阶段单独成态，且不显示切片百分比", () => {
    const st = deriveSubtitleState(
      row({
        subtitle_status: "generating",
        subtitle_phase: "merging",
        subtitle_slices_done: 45,
        subtitle_slices_total: 45,
      }),
    );
    expect(st.kind).toBe("merging");
    expect(st.percent).toBeUndefined();
  });

  it("降级轨：拿不到切片总数时给计时，绝不编造百分比", () => {
    const st = deriveSubtitleState(
      row({ subtitle_status: "generating", subtitle_started_at: 1_700_000_000 }),
    );
    expect(st.kind).toBe("transcribing");
    expect(st.percent).toBeUndefined();
    expect(st.startedAt).toBe(1_700_000_000);
    expect(st.label).toContain("正在生成字幕");
    // 关键断言：降级态的 label 里不能出现百分号
    expect(st.label).not.toContain("%");
  });
});

describe("formatElapsed（降级轨计时显示）", () => {
  it("秒 → mm:ss，补零且向下取整", () => {
    expect(formatElapsed(0)).toBe("00:00");
    expect(formatElapsed(9)).toBe("00:09");
    expect(formatElapsed(84)).toBe("01:24");
    expect(formatElapsed(84.9)).toBe("01:24");
    expect(formatElapsed(3600)).toBe("60:00");
  });

  it("负数与非法值不崩，兜底 00:00", () => {
    expect(formatElapsed(-5)).toBe("00:00");
    expect(formatElapsed(NaN)).toBe("00:00");
  });
});

describe("pickBatchIds（PRD v8 §5.5A.5 状态白名单）", () => {
  const rows = [
    row({ course_id: "a", subtitle_status: "pending" }),
    row({ course_id: "b", subtitle_status: "error" }),
    row({ course_id: "c", subtitle_status: "generating", subtitle_task_active: true }),
    row({ course_id: "d", subtitle_status: "ready", review_state: "unreviewed" }),
    row({ course_id: "e", subtitle_status: "ready", review_state: "reviewed" }),
  ];

  it("按状态把选中行分到四个桶，互不串台", () => {
    const r = pickBatchIds(rows);
    expect(r.generateIds).toEqual(["a", "b"]); // pending + error 才可生成
    expect(r.cancelIds).toEqual(["c"]); // 只有真实任务仍活跃的 generating 可取消
    expect(r.reviewableIds).toEqual(["d"]); // ready 且未校对 → 可标记
    expect(r.unreviewableIds).toEqual(["e"]); // ready 且已校对 → 可撤销
  });

  it("空选择返回四个空数组，批量按钮据此不渲染", () => {
    const r = pickBatchIds([]);
    expect(r).toEqual({
      generateIds: [],
      cancelIds: [],
      reviewableIds: [],
      unreviewableIds: [],
    });
  });

  it("已校对的行不会同时出现在「标记」桶里，避免后端 400", () => {
    const r = pickBatchIds([rows[4]]);
    expect(r.reviewableIds).toEqual([]);
    expect(r.unreviewableIds).toEqual(["e"]);
  });

  it("生成中的行不可再次生成（后端白名单同口径）", () => {
    const r = pickBatchIds([rows[2]]);
    expect(r.generateIds).toEqual([]);
    expect(r.cancelIds).toEqual(["c"]);
  });
});

describe("固定 7 展示态、选择规则与模型加载相位（v9 §5.5A.3 / §5.5A.4）", () => {
  it("error 只表示生成失败，取消不形成持久展示态", () => {
    expect(deriveSubtitleState({ subtitle_status: "error" }).kind).toBe("error");
  });

  it("canSelect 按动作限制可选行，取消还要求真实 runtime 任务", () => {
    expect(canSelect({ subtitle_status: "pending" }, "generate")).toBe(true);
    expect(canSelect({ subtitle_status: "error" }, "generate")).toBe(true);
    expect(canSelect({ subtitle_status: "ready" }, "generate")).toBe(false);
    expect(
      canSelect({ subtitle_status: "generating", subtitle_task_active: true }, "cancel"),
    ).toBe(true);
    expect(
      canSelect({ subtitle_status: "generating", subtitle_task_active: false }, "cancel"),
    ).toBe(false);
    expect(
      canSelect({ subtitle_status: "ready", review_state: "unreviewed" }, "review", "mark"),
    ).toBe(true);
    expect(
      canSelect({ subtitle_status: "ready", review_state: "reviewed" }, "review", "mark"),
    ).toBe(false);
    expect(
      canSelect({ subtitle_status: "ready", review_state: "reviewed" }, "review", "revoke"),
    ).toBe(true);
    expect(
      canSelect({ subtitle_status: "ready", review_state: "unreviewed" }, "review", "revoke"),
    ).toBe(false);
    expect(canSelect({ subtitle_status: "ready" }, "review")).toBe(false);
    expect(canSelect({ subtitle_status: "ready" }, null)).toBe(false);
  });

  it("模型加载阶段文案诚实，且 startedAt 有值", () => {
    // 拿不到切片总数时计时是唯一的进展信号；若 startedAt 为空，
    // 降级轨只剩孤零零一行文案，与卡死无法区分。
    const st = deriveSubtitleState({
      subtitle_status: "generating",
      subtitle_phase: "loading_model",
      subtitle_started_at: 1_700_000_000,
    });
    expect(st.kind).toBe("transcribing");
    expect(st.label).toContain("加载模型");
    expect(st.startedAt).toBe(1_700_000_000);
    expect(st.percent).toBeUndefined(); // 绝不编造百分比
  });

  it("非加载阶段的降级轨仍显示「正在生成字幕」", () => {
    const st = deriveSubtitleState({
      subtitle_status: "generating",
      subtitle_phase: null,
      subtitle_started_at: 1_700_000_000,
    });
    expect(st.label).toContain("正在生成字幕");
  });

  it("审核判定只有一把尺子：展示态与批量过滤不得打架", () => {
    // review_state 缺失时按未校对兼容，旧批量接口仍应能把它算进可标记集合。
    const row = { subtitle_status: "ready", review_state: undefined };
    expect(deriveSubtitleState(row).kind).toBe("unreviewed");
    expect(pickBatchIds([{ ...row, course_id: "z" } as any]).reviewableIds).toEqual(["z"]);
  });
});
