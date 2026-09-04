/**CC（字幕）开关偏好：localStorage 持久化。默认显示。*/
const CC_VISIBLE_KEY = "ai-study-cc-visible";

export function loadCcVisible(): boolean {
  const raw = localStorage.getItem(CC_VISIBLE_KEY);
  if (raw === null) return true; // 默认显示字幕
  return raw === "true";
}

export function saveCcVisible(visible: boolean): void {
  try {
    localStorage.setItem(CC_VISIBLE_KEY, String(visible));
  } catch {
    /* localStorage 不可用时静默忽略 */
  }
}
