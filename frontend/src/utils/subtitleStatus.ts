/** 字幕展示态派生（PRD v8 §5.5A.3 状态机 / §5.5A.4 进度与降级轨）。

后端 `subtitle_status` 只有 4 个真值（pending / generating / ready / error），
前端据此派生 7 种展示态。抽成纯函数是为了能脱离 React 单测 —— 展示态的分支多，
靠肉眼点页面回归太容易漏。

**降级轨的原则**：拿不到 `slices_total`（如视频时长探测失败）时，显示
"⏳ 正在生成字幕… + 已运行 mm:ss"，**绝不编造百分比**。
一个假的 78% 比一个诚实的计时器更有害 —— 用户会以为它真的在推进。
*/

export type SubtitleViewKind =
  | "pending"
  | "queued"
  | "transcribing"
  | "merging"
  | "unreviewed"
  | "reviewed"
  | "error";

export type BatchMode = "generate" | "cancel" | "review" | null;
export type ReviewTarget = "mark" | "revoke" | null;

export interface SubtitleViewState {
  kind: SubtitleViewKind;
  label: string;
  /** 0~100；仅 transcribing 且拿得到切片总数时有值 */
  percent?: number;
  slicesDone?: number;
  slicesTotal?: number;
  queuePosition?: number;
  /** 降级轨用：Unix 秒，前端据此显示"已运行 mm:ss" */
  startedAt?: number;
}

/** 派生所需的最小字段集（MaterialRow 的子集，便于单测造数据）。 */
export interface SubtitleRuntimeFields {
  subtitle_status?: string;
  review_state?: string;
  subtitle_task_active?: boolean;
  subtitle_phase?: string | null;
  subtitle_slices_done?: number;
  subtitle_slices_total?: number;
  subtitle_started_at?: number | null;
  subtitle_queue_position?: number;
}

/** 人工校对状态判定；它只表达质量，不控制播放器或 AI Evidence 准入。 */
export function isReviewed(row: SubtitleRuntimeFields): boolean {
  return row.review_state === "reviewed";
}

export function deriveSubtitleState(row: SubtitleRuntimeFields): SubtitleViewState {
  const s = row.subtitle_status;

  if (s === "error") {
    return { kind: "error", label: "生成失败" };
  }

  if (s === "ready") {
    return isReviewed(row)
      ? { kind: "reviewed", label: "已生成" }
      : { kind: "unreviewed", label: "已生成" };
  }

  if (s === "generating") {
    // 排队位次优先：还在排队就别报进度，报了也是 0%，看着像卡死
    const qp = row.subtitle_queue_position ?? 0;
    if (qp > 0) {
      return { kind: "queued", label: `排队中（第 ${qp} 位）`, queuePosition: qp };
    }
    // 切片跑完、正在合并写盘：这段没有切片进度可报
    if (row.subtitle_phase === "merging") {
      return { kind: "merging", label: "合并字幕…" };
    }
    const total = row.subtitle_slices_total ?? 0;
    if (total > 0) {
      const done = row.subtitle_slices_done ?? 0;
      const percent = Math.min(100, Math.round((done / total) * 100));
      return {
        kind: "transcribing",
        label: `生成中 ${percent}%`,
        percent,
        slicesDone: done,
        slicesTotal: total,
      };
    }
    // 降级轨：拿不到切片总数 → 给计时，不给百分比。
    // loading_model 阶段文案必须诚实 —— 此刻在下载/加载 Whisper 模型（首次可达数分钟），
    // 既没有切片总数也没有转写进度，说"正在生成字幕"是撒谎。
    return {
      kind: "transcribing",
      label:
        row.subtitle_phase === "loading_model"
          ? "⏳ 正在加载模型…"
          : "⏳ 正在生成字幕…",
      startedAt: row.subtitle_started_at ?? undefined,
    };
  }

  return { kind: "pending", label: "待生成" };
}

/** 秒 → mm:ss（降级轨的"已运行"显示用）。 */
export function formatElapsed(sec: number): string {
  const s = Math.max(0, Math.floor(Number(sec) || 0));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
}

export function canSelect(
  row: SubtitleRuntimeFields,
  mode: BatchMode,
  reviewTarget: ReviewTarget = null,
): boolean {
  if (mode === "generate") {
    return row.subtitle_status === "pending" || row.subtitle_status === "error";
  }
  if (mode === "cancel") {
    return row.subtitle_status === "generating" && row.subtitle_task_active === true;
  }
  if (mode === "review") {
    if (row.subtitle_status !== "ready") return false;
    if (reviewTarget === "mark") return !isReviewed(row);
    if (reviewTarget === "revoke") return isReviewed(row);
    return false;
  }
  return false;
}

/**
 * 批量操作的合法 ID 集合（PRD v8 §5.5A.5）。
 *
 * 前端按这个过滤要提交的 course_ids —— 按钮上显示的计数也来自这里。
 * 后端用同一份白名单二次校验，前端过滤只是为了少发无用请求，不是安全边界。
 */
export function pickBatchIds(rows: SubtitleRuntimeFields[]) {
  return {
    generateIds: rows
      .filter((r) => r.subtitle_status === "pending" || r.subtitle_status === "error")
      .map((r) => (r as any).course_id as string),
    cancelIds: rows
      .filter((r) => canSelect(r, "cancel"))
      .map((r) => (r as any).course_id as string),
    reviewableIds: rows
      .filter((r) => r.subtitle_status === "ready" && !isReviewed(r))
      .map((r) => (r as any).course_id as string),
    unreviewableIds: rows
      .filter((r) => r.subtitle_status === "ready" && isReviewed(r))
      .map((r) => (r as any).course_id as string),
  };
}
