import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { Dropdown, message, Spin } from "antd";
import Artplayer from "artplayer";
import TopNav from "../components/TopNav";
import SubtitleOverlay, { Cue, parseVTT } from "../components/SubtitleOverlay";
import AISidebar from "../components/AISidebar";
import { getToken } from "../store/auth";
import { loadProgress, saveProgress } from "../store/progress";

const AI_WIDTH_KEY = "ai-study-sidebar-width";
const MIN_AI_WIDTH = 30;
// 上限与默认值同为 50%：AI 侧边栏最多占半屏，保证视频区始终有一半可见
const MAX_AI_WIDTH = 50;

function clampAiWidth(value: number) {
  return Math.min(MAX_AI_WIDTH, Math.max(MIN_AI_WIDTH, value));
}

function loadAiWidth() {
  const saved = Number(localStorage.getItem(AI_WIDTH_KEY));
  return Number.isFinite(saved) && saved > 0 ? clampAiWidth(saved) : 50;
}

// 右键菜单状态
interface MenuState {
  visible: boolean;
  x: number;
  y: number;
  selectedText: string;
  mode: "L1" | "L2" | "L3";
  cue: Cue | null;
  time: number;
}

export default function Player() {
  const { courseId } = useParams<{ courseId: string }>();
  const artRef = useRef<Artplayer | null>(null);
  const videoRef = useRef<HTMLDivElement>(null);
  const workspaceRef = useRef<HTMLDivElement>(null);
  const draggingRef = useRef(false);
  const aiWidthRef = useRef(loadAiWidth());
  const [cues, setCues] = useState<Cue[]>([]);
  const [currentTime, setCurrentTime] = useState(0);
  const [videoDuration, setVideoDuration] = useState<number | null>(null);
  const [currentCue, setCurrentCue] = useState<Cue | null>(null);
  const [aiExpanded, setAiExpanded] = useState(true);
  const [menu, setMenu] = useState<MenuState | null>(null);
  const [prefill, setPrefill] = useState("");
  const [selSubtitle, setSelSubtitle] = useState("");
  const [selTime, setSelTime] = useState<number | null>(null);
  const [videoLoading, setVideoLoading] = useState(true);
  const [videoError, setVideoError] = useState("");
  const [aiWidth, setAiWidth] = useState(aiWidthRef.current);
  const [resizing, setResizing] = useState(false);

  const resizeFromPointer = (clientX: number) => {
    const bounds = workspaceRef.current?.getBoundingClientRect();
    if (!bounds?.width) return;
    const next = clampAiWidth(((bounds.right - clientX) / bounds.width) * 100);
    aiWidthRef.current = next;
    setAiWidth(next);
  };

  const saveAiWidth = (value: number) => {
    const next = clampAiWidth(value);
    aiWidthRef.current = next;
    setAiWidth(next);
    localStorage.setItem(AI_WIDTH_KEY, String(next));
  };

  // 加载字幕
  useEffect(() => {
    if (!courseId) return;
    const controller = new AbortController();
    setCues([]);
    setCurrentCue(null);

    fetch(`/api/materials/${courseId}/subtitle`, {
      headers: { Authorization: `Bearer ${getToken()}` },
      signal: controller.signal,
    })
      .then((response) => (response.ok ? response.text() : null))
      .then((vtt) => {
        if (vtt) setCues(parseVTT(vtt));
      })
      .catch((error: Error) => {
        if (error.name !== "AbortError") setCues([]);
      });

    return () => controller.abort();
  }, [courseId]);

  // 初始化 ArtPlayer
  useEffect(() => {
    if (!courseId || !videoRef.current) return;

    // 先用 JWT 换取课程绑定的短期播放 URL，再由 video 标签原生发起 Range 请求。
    const controller = new AbortController();
    let disposed = false;
    let art: Artplayer | null = null;
    let lastSave = 0;
    setVideoLoading(true);
    setVideoError("");
    setCurrentTime(0);
    setVideoDuration(null);

    fetch(`/api/materials/${courseId}/playback-ticket`, {
      method: "POST",
      headers: { Authorization: `Bearer ${getToken()}` },
      signal: controller.signal,
    })
      .then((response) => {
        if (!response.ok) throw new Error(`playback ticket failed: ${response.status}`);
        return response.json() as Promise<{ url: string }>;
      })
      .then(({ url }) => {
        if (disposed) return;
        const player = new Artplayer({
          container: videoRef.current!,
          url,
          autoplay: false,
          volume: 0.7,
          playbackRate: true,
          setting: true,
          hotkey: true,
          fullscreenWeb: true,
          fullscreen: true,
          poster: "",
        });
        art = player;
        artRef.current = player;
        player.on("ready", () => {
          if (disposed) return;
          setVideoLoading(false);
          setVideoDuration(Number.isFinite(player.duration) ? player.duration : null);
          // 恢复上次学习进度
          const saved = loadProgress(courseId);
          if (saved && saved.time > 1) {
            player.seek = saved.time;
          }
        });
        player.on("video:timeupdate", () => {
          if (disposed) return;
          setCurrentTime(player.currentTime);
          const now = Date.now();
          if (now - lastSave > 3000) {
            lastSave = now;
            saveProgress(courseId, player.currentTime);
          }
        });
        player.on("video:error", () => {
          if (disposed) return;
          setVideoLoading(false);
          setVideoError("视频加载失败，请稍后重试");
        });
      })
      .catch((error: Error) => {
        if (disposed || error.name === "AbortError") return;
        setVideoLoading(false);
        setVideoError("视频加载失败，请稍后重试");
      });

    return () => {
      disposed = true;
      controller.abort();
      art?.destroy(false);
      if (artRef.current === art) artRef.current = null;
    };
  }, [courseId]);

  // 右键菜单
  const onContextMenu = (e: React.MouseEvent) => {
    e.preventDefault();
    const selected = window.getSelection()?.toString().trim() || "";
    const cue = currentCue;
    const time = artRef.current?.currentTime ?? 0;

    if (selected) {
      // L1：选中字幕
      setMenu({ visible: true, x: e.clientX, y: e.clientY, selectedText: selected, mode: "L1", cue, time });
    } else if (cue) {
      // L2：取当前 cue 整条
      setMenu({ visible: true, x: e.clientX, y: e.clientY, selectedText: cue.text, mode: "L2", cue, time });
    } else {
      // L3：只传时间戳
      setMenu({ visible: true, x: e.clientX, y: e.clientY, selectedText: "", mode: "L3", cue: null, time });
    }
  };

  const closeMenu = () => setMenu(null);

  const askAI = (m: MenuState) => {
    const art = artRef.current;
    art?.pause();
    setAiExpanded(true);
    setSelSubtitle(m.selectedText);
    setSelTime(m.mode === "L3" ? m.time : m.cue?.start ?? m.time);
    if (m.mode === "L1") {
      setPrefill(`用户看到了「${m.selectedText}」，疑问是：`);
    } else if (m.mode === "L2") {
      setPrefill(`用户看到了「${m.selectedText}」，疑问是：`);
      message.info("已使用整条字幕");
    } else {
      setPrefill(`用户看到了当前时间点（${Math.floor(m.time)}s）的内容，疑问是：`);
      message.info("已使用当前时间点上下文");
    }
    closeMenu();
  };

  const menuLabel = useMemo(() => {
    if (!menu) return "";
    if (menu.mode === "L1") return "以此段字幕向 AI 提问";
    if (menu.mode === "L2") return "以当前时间点字幕向 AI 提问";
    return "以当前播放时间点向 AI 提问";
  }, [menu]);

  // 全局点击关闭菜单
  useEffect(() => {
    window.addEventListener("click", closeMenu);
    return () => window.removeEventListener("click", closeMenu);
  }, []);

  return (
    <div className="player-page">
      <TopNav aiExpanded={aiExpanded} onToggleAI={() => setAiExpanded((open) => !open)} />
      <div ref={workspaceRef} className="player-workspace">
        {/* 左：视频区 */}
        <main className="player-stage">
          <div className="player-video-frame">
            <div
              ref={videoRef}
              className="player-video-surface"
              data-testid="video-surface"
              onContextMenu={onContextMenu}
            />
            {videoLoading && (
              <div className="player-video-status">
                <Spin />
                <span>视频加载中…</span>
              </div>
            )}
            {videoError && (
              <div className="player-video-status player-video-status--error" role="alert">
                {videoError}
              </div>
            )}
            {cues.length > 0 && (
              <SubtitleOverlay
                currentTime={currentTime}
                cues={cues}
                onCueChange={(c) => setCurrentCue(c)}
              />
            )}
          </div>
        </main>

        {/* 右：AI 侧边栏 */}
        {aiExpanded && (
          <div
            className={`player-splitter${resizing ? " player-splitter--active" : ""}`}
            role="separator"
            aria-label="调整 AI 对话宽度"
            aria-orientation="vertical"
            aria-valuemin={MIN_AI_WIDTH}
            aria-valuemax={MAX_AI_WIDTH}
            aria-valuenow={Math.round(aiWidth)}
            tabIndex={0}
            onPointerDown={(event) => {
              event.preventDefault();
              draggingRef.current = true;
              setResizing(true);
              event.currentTarget.setPointerCapture(event.pointerId);
            }}
            onPointerMove={(event) => {
              if (draggingRef.current) resizeFromPointer(event.clientX);
            }}
            onPointerUp={(event) => {
              if (!draggingRef.current) return;
              resizeFromPointer(event.clientX);
              draggingRef.current = false;
              setResizing(false);
              event.currentTarget.releasePointerCapture(event.pointerId);
              const bounds = workspaceRef.current?.getBoundingClientRect();
              if (bounds?.width) {
                saveAiWidth(((bounds.right - event.clientX) / bounds.width) * 100);
              }
            }}
            onPointerCancel={() => {
              draggingRef.current = false;
              setResizing(false);
              saveAiWidth(aiWidthRef.current);
            }}
            onKeyDown={(event) => {
              if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
              event.preventDefault();
              saveAiWidth(aiWidth + (event.key === "ArrowLeft" ? 2 : -2));
            }}
          />
        )}
        <div
          className={`player-ai-panel${aiExpanded ? "" : " player-ai-panel--collapsed"}${resizing ? " player-ai-panel--resizing" : ""}`}
          aria-hidden={!aiExpanded}
          style={{ width: aiExpanded ? `${aiWidth}%` : 0 }}
        >
          <AISidebar
            courseId={courseId || ""}
            prefill={prefill}
            selectedSubtitle={selSubtitle}
            startTime={selTime}
            endTime={selTime}
            currentTime={currentTime}
            videoDuration={videoDuration}
            onContextConsumed={() => {
              setSelSubtitle("");
              setSelTime(null);
            }}
          />
        </div>
      </div>

      {/* 右键菜单 */}
      {menu?.visible && (
        <Dropdown
          open={menu.visible}
          onOpenChange={(open) => !open && closeMenu()}
          menu={{
            items: [{ key: "ask", label: menuLabel, onClick: () => askAI(menu) }],
          }}
        >
          <div style={{ position: "fixed", left: menu.x, top: menu.y, zIndex: 1000, width: 1, height: 1 }} />
        </Dropdown>
      )}
    </div>
  );
}
