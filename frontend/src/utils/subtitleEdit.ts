/** 字幕编辑器（P4）与人工抽查（P5）共用的纯函数，便于单测，且与后端校验保持一致。 */

export interface EditCue {
  start: number;
  end: number;
  text: string;
}

/** 秒 → mm:ss（展示用）。 */
export function secToStr(sec: number): string {
  const s = Math.max(0, Math.floor(Number(sec) || 0));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
}

export interface CueIssue {
  index: number;
  reason: string;
}

/** 时间轴合法性校验（前后端一致）：返回非法原因数组，空数组=合法。 */
export function validateCueAxis(cues: EditCue[]): CueIssue[] {
  const issues: CueIssue[] = [];
  cues.forEach((c, i) => {
    const s = Number(c.start);
    const e = Number(c.end);
    if (Number.isNaN(s) || Number.isNaN(e)) {
      issues.push({ index: i, reason: "开始/结束时间不是数字" });
      return;
    }
    if (!Number.isFinite(s) || !Number.isFinite(e)) {
      issues.push({ index: i, reason: "时间戳非法（NaN/Inf）" });
      return;
    }
    if (s < 0) {
      issues.push({ index: i, reason: "开始时间不能为负" });
      return;
    }
    if (e <= s) {
      issues.push({ index: i, reason: "结束时间必须大于开始时间" });
    }
  });
  return issues;
}

/** P5 人工抽查辅助：标记可疑 cue（超长 / 空文本），返回原因或 null。 */
export function suspiciousReason(c: EditCue): string | null {
  const dur = Number(c.end) - Number(c.start);
  if (Number.isFinite(dur) && dur > 12) return "超长字幕(>12s)";
  if (!c.text || !c.text.trim()) return "空文本";
  return null;
}
