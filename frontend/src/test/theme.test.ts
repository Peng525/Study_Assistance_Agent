import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  getStoredTheme,
  setStoredTheme,
  resolveTheme,
  systemPrefersDark,
} from "../theme/theme";

describe("主题系统", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("默认返回 system", () => {
    expect(getStoredTheme()).toBe("system");
  });

  it("setStoredTheme 持久化", () => {
    setStoredTheme("dark");
    expect(getStoredTheme()).toBe("dark");
    expect(localStorage.getItem("ai-study-theme")).toBe("dark");
  });

  it("resolveTheme light/dark 直接返回", () => {
    expect(resolveTheme("light")).toBe("light");
    expect(resolveTheme("dark")).toBe("dark");
  });

  it("resolveTheme system 跟随 OS", () => {
    // jsdom 默认 prefers-color-scheme 为 light
    expect(resolveTheme("system")).toBe("light");
  });
});
