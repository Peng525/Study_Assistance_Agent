"""视频展示名称：只采用显式标题和原始文件名，不推断课程 ID 或课件正文。"""
from pathlib import PureWindowsPath


def resolve_display_title(title: str | None, filename: str | None) -> str:
    if title and title.strip():
        return title.strip()
    stem = PureWindowsPath(filename).stem.strip() if filename else ""
    return stem or "当前视频"
