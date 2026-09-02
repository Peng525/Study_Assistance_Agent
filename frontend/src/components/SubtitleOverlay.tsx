import { useEffect, useRef, useState } from "react";

// 字幕 cue 数据结构
export interface Cue {
  start: number;
  end: number;
  text: string;
}

interface SubtitleOverlayProps {
  currentTime: number;
  cues: Cue[];
  onCueChange?: (cue: Cue | null) => void;
}

// 解析 VTT 文本
export function parseVTT(vtt: string): Cue[] {
  const cues: Cue[] = [];
  const lines = vtt.split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i].trim();
    if (line.includes("-->")) {
      const [startStr, endStr] = line.split("-->").map((s) => s.trim());
      const start = tsToSeconds(startStr);
      const end = tsToSeconds(endStr);
      i++;
      const textLines: string[] = [];
      while (i < lines.length && lines[i].trim() !== "") {
        textLines.push(lines[i].trim());
        i++;
      }
      cues.push({ start, end, text: textLines.join("\n") });
      continue;
    }
    i++;
  }
  return cues;
}

export function tsToSeconds(ts: string): number {
  const parts = ts.replace(",", ".").split(":").map(Number);
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
  if (parts.length === 2) return parts[0] * 60 + parts[1];
  return parts[0] || 0;
}

export default function SubtitleOverlay({ currentTime, cues, onCueChange }: SubtitleOverlayProps) {
  const [currentCue, setCurrentCue] = useState<Cue | null>(null);
  const cueRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const cue = cues.find((c) => currentTime >= c.start && currentTime <= c.end) || null;
    setCurrentCue(cue);
    onCueChange?.(cue);
  }, [currentTime, cues, onCueChange]);

  if (!currentCue) return null;

  return (
    <div
      ref={cueRef}
      className="subtitle-overlay"
      data-start={currentCue?.start}
      data-end={currentCue?.end}
      style={{
        position: "absolute",
        bottom: 48,
        left: "50%",
        transform: "translateX(-50%)",
        maxWidth: "80%",
        padding: "6px 14px",
        borderRadius: 6,
        background: "var(--subtitle-bg)",
        color: "var(--subtitle-text)",
        fontSize: 18,
        lineHeight: 1.5,
        textAlign: "center",
        pointerEvents: "auto",
        userSelect: "text",
        cursor: "text",
        zIndex: 10,
        whiteSpace: "pre-wrap",
      }}
    >
      {currentCue.text}
    </div>
  );
}
