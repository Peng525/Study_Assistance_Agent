import { describe, it, expect } from "vitest";
import { parseVTT, tsToSeconds } from "../components/SubtitleOverlay";

const VTT = `WEBVTT

00:00:10.000 --> 00:00:15.000
第一段

00:01:00.000 --> 00:01:30.000
第二段
多行内容
`;

describe("字幕解析", () => {
  it("parseVTT 解析 cue", () => {
    const cues = parseVTT(VTT);
    expect(cues.length).toBe(2);
    expect(cues[0].text).toBe("第一段");
    expect(cues[0].start).toBe(10);
    expect(cues[1].text).toContain("多行内容");
  });

  it("tsToSeconds 处理时分秒", () => {
    expect(tsToSeconds("00:01:30.500")).toBe(90.5);
    expect(tsToSeconds("01:00")).toBe(60);
  });
});
