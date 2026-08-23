import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { Button, Dropdown, message, Spin } from "antd";
import { MenuFoldOutlined, MenuUnfoldOutlined, LeftOutlined } from "@ant-design/icons";
import Artplayer from "artplayer";
import { useNavigate } from "react-router-dom";
import TopNav from "../components/TopNav";
import SubtitleOverlay, { Cue, parseVTT } from "../components/SubtitleOverlay";
import AISidebar from "../components/AISidebar";
import { getToken } from "../store/auth";

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
  const navigate = useNavigate();
  const artRef = useRef<Artplayer | null>(null);
  const videoRef = useRef<HTMLDivElement>(null);
  const [cues, setCues] = useState<Cue[]>([]);
  const [currentTime, setCurrentTime] = useState(0);
  const [currentCue, setCurrentCue] = useState<Cue | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [menu, setMenu] = useState<MenuState | null>(null);
  const [prefill, setPrefill] = useState("");
  const [selSubtitle, setSelSubtitle] = useState("");
  const [selTime, setSelTime] = useState<number | null>(null);
  const [subtitleFailed, setSubtitleFailed] = useState(false);
  const [videoUrl, setVideoUrl] = useState("");
  const [loading, setLoading] = useState(true);

  // 加载字幕
  useEffect(() => {
    if (!courseId) return;
    fetch(`/api/materials/${courseId}/subtitle`, {
      headers: { Authorization: `Bearer ${getToken()}` },
    })
      .then((r) => {
        if (r.status === 404) {
          // 无字幕 → 可能 Whisper 生成中，标记 L4 兜底
          setSubtitleFailed(true);
          return null;
        }
        return r.text();
      })
      .then((vtt) => {
        if (vtt) setCues(parseVTT(vtt));
        else setSubtitleFailed(true);
      })
      .catch(() => setSubtitleFailed(true));
  }, [courseId]);

  // 初始化 ArtPlayer
  useEffect(() => {
    if (!courseId || !videoRef.current) return;

    // 视频流需携带 JWT，通过 fetch 获取 blob URL（video 标签无法自定义 header）
    let blobUrl = "";
    const art = new Artplayer({
      container: videoRef.current,
      url: "",
      autoplay: false,
      volume: 0.7,
      poster: "",
      customType: {
        mp4: (video: HTMLVideoElement, url: string) => {
          video.src = blobUrl;
        },
      },
    });

    fetch(`/api/materials/${courseId}/video`, {
      headers: { Authorization: `Bearer ${getToken()}` },
    })
      .then((r) => r.blob())
      .then((blob) => {
        blobUrl = URL.createObjectURL(blob);
        art.switchUrl("/api/materials/" + courseId + "/video");
        art.on("ready", () => setLoading(false));
      })
      .catch(() => setLoading(false));

    artRef.current = art;
    art.on("video:timeupdate", () => {
      setCurrentTime(art.currentTime);
    });
    setLoading(false);

    return () => {
      if (blobUrl) URL.revokeObjectURL(blobUrl);
      art.destroy(false);
      artRef.current = null;
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
    setCollapsed(false);
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
    <div style={{ height: "100%", display: "flex", flexDirection: "column", background: "var(--bg)" }}>
      <TopNav />
      <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
        {/* 左：视频区 */}
        <div style={{ flex: 1, position: "relative", background: "var(--bg-video)", minWidth: 0 }}>
          <div style={{ position: "absolute", top: 8, left: 8, zIndex: 20 }}>
            <Button icon={<LeftOutlined />} onClick={() => navigate("/")} ghost size="small">
              返回
            </Button>
          </div>
          <div ref={videoRef} style={{ width: "100%", height: "100%" }} onContextMenu={onContextMenu} />
          {!subtitleFailed && (
            <SubtitleOverlay
              currentTime={currentTime}
              cues={cues}
              onCueChange={(c) => setCurrentCue(c)}
            />
          )}
          {subtitleFailed && (
            <div style={{ position: "absolute", bottom: 48, left: 0, right: 0, textAlign: "center", color: "#fff" }}>
              字幕交互降级为手动模式（字幕生成中或不可用）
            </div>
          )}
        </div>

        {/* 右：AI 侧边栏 */}
        <div style={{ display: "flex", flexDirection: "row" }}>
          {!collapsed && (
            <AISidebar
              courseId={courseId || ""}
              prefill={prefill}
              selectedSubtitle={selSubtitle}
              startTime={selTime}
              endTime={selTime}
            />
          )}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              padding: "0 4px",
              background: "var(--bg-header)",
              borderLeft: "1px solid var(--border)",
            }}
          >
            <Button
              type="text"
              icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              onClick={() => setCollapsed(!collapsed)}
            />
          </div>
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
