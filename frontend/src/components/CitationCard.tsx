import { formatTime } from "../utils/time";

/**用户主动引用的一条字幕（Active Citation）：文本 + 在视频中的真实时间区间。*/
export interface Citation {
  text: string;
  start: number;
  end: number;
}

/**
 * 引用卡片：展示当前这一轮 AI 提问所引用的字幕片段与时间区间。
 * 与「自动 ±180 秒 Transcript Context」是两回事——这里是用户**主动**选中的，
 * 未审核字幕也允许主动引用（仅不允许自动注入上下文）。
 */
export default function CitationCard({
  citation,
  onRemove,
}: {
  citation: Citation;
  onRemove: () => void;
}) {
  return (
    <div className="citation-card" data-testid="citation-card">
      <div className="citation-card__meta">
        <span className="citation-card__time">
          字幕引用 {formatTime(citation.start)}–{formatTime(citation.end)}
        </span>
        <button
          type="button"
          className="citation-card__close"
          aria-label="移除引用"
          onClick={onRemove}
        >
          ×
        </button>
      </div>
      <div className="citation-card__text">{citation.text}</div>
    </div>
  );
}
