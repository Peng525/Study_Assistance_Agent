import { describe, it, expect } from "vitest";
import { validateCueAxis, suspiciousReason, secToStr, EditCue } from "../utils/subtitleEdit";

const c = (start: number, end: number, text = "x"): EditCue => ({ start, end, text });

describe("validateCueAxis", () => {
  it("接受合法时间轴", () => {
    expect(validateCueAxis([c(0, 5), c(5, 10)])).toHaveLength(0);
  });

  it("拒绝结束<=开始", () => {
    const issues = validateCueAxis([c(5, 2)]);
    expect(issues).toHaveLength(1);
    expect(issues[0].reason).toContain("结束时间必须大于开始时间");
  });

  it("拒绝负开始时间", () => {
    const issues = validateCueAxis([c(-1, 2)]);
    expect(issues[0].reason).toContain("不能为负");
  });

  it("拒绝非数字时间戳", () => {
    const issues = validateCueAxis([{ start: NaN, end: 5, text: "x" } as any]);
    expect(issues[0].reason).toContain("数字");
  });

  it("拒绝无穷大", () => {
    const issues = validateCueAxis([{ start: 0, end: Infinity, text: "x" } as any]);
    expect(issues[0].reason).toContain("非法");
  });
});

describe("suspiciousReason (P5 抽查辅助)", () => {
  it("标记超长字幕", () => {
    expect(suspiciousReason(c(0, 13))).toContain("超长");
  });
  it("标记空文本", () => {
    expect(suspiciousReason(c(0, 5, ""))).toBe("空文本");
  });
  it("正常字幕返回 null", () => {
    expect(suspiciousReason(c(0, 5, "你好"))).toBeNull();
  });
});

describe("secToStr", () => {
  it("秒转 mm:ss", () => {
    expect(secToStr(65)).toBe("01:05");
    expect(secToStr(0)).toBe("00:00");
    expect(secToStr(125)).toBe("02:05");
  });
});
