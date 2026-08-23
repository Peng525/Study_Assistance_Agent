// 学习进度记录（localStorage，Phase 0 轻量实现）
export interface ProgressRecord {
  courseId: string;
  time: number; // 播放位置（秒）
  updatedAt: number; // 时间戳
}

const KEY = "ai-study-progress";

export function saveProgress(courseId: string, time: number): void {
  const all = loadAll();
  all[courseId] = { courseId, time, updatedAt: Date.now() };
  localStorage.setItem(KEY, JSON.stringify(all));
}

export function loadProgress(courseId: string): ProgressRecord | null {
  const all = loadAll();
  return all[courseId] || null;
}

export function loadAll(): Record<string, ProgressRecord> {
  try {
    return JSON.parse(localStorage.getItem(KEY) || "{}");
  } catch {
    return {};
  }
}

// 返回最近一次学习的课程（用于 Hero"继续学习"）
export function latestProgress(): ProgressRecord | null {
  const all = Object.values(loadAll());
  if (all.length === 0) return null;
  return all.sort((a, b) => b.updatedAt - a.updatedAt)[0];
}
