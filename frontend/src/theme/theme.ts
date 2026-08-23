// 主题类型与常量（模块 4.3 视频主题 3 模式）
export type ThemeMode = "light" | "dark" | "system";

export const THEME_STORAGE_KEY = "ai-study-theme";

export function getStoredTheme(): ThemeMode {
  const v = localStorage.getItem(THEME_STORAGE_KEY);
  if (v === "light" || v === "dark" || v === "system") return v;
  return "system";
}

export function setStoredTheme(mode: ThemeMode): void {
  localStorage.setItem(THEME_STORAGE_KEY, mode);
}

// 系统是否偏好深色
export function systemPrefersDark(): boolean {
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

// 计算实际生效主题（system 模式时跟随 OS）
export function resolveTheme(mode: ThemeMode): "light" | "dark" {
  if (mode === "system") return systemPrefersDark() ? "dark" : "light";
  return mode;
}
