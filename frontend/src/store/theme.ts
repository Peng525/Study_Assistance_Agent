import { create } from "zustand";
import { getStoredTheme, setStoredTheme, ThemeMode } from "../theme/theme";

export { resolveTheme } from "../theme/theme";

interface ThemeState {
  mode: ThemeMode;
  setMode: (m: ThemeMode) => void;
}

export const useThemeStore = create<ThemeState>((set) => ({
  mode: getStoredTheme(),
  setMode: (m) => {
    setStoredTheme(m);
    set({ mode: m });
  },
}));
