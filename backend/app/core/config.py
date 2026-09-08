"""应用配置模块。

从项目根目录 .env 读取配置，通过 pydantic-settings 管理。
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（backend/ 的上一级），.env 位于根目录
# config.py 位于 backend/app/core/，向上 4 级到根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


class Settings(BaseSettings):
    """应用配置。"""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 应用
    app_name: str = "AI 助学助手"
    app_port: int = 8000
    debug: bool = False

    # CORS 允许的源
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # 大模型
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_api_key: str = ""
    llm_model_name: str = "qwen-plus"

    # 管理台账户（Phase 0 预置账号，生产环境应通过 .env 覆盖）
    admin_username: str = "admin"
    admin_password: str = ""
    user_username: str = "user25"
    user_password: str = ""

    # JWT（生产环境必须通过 .env 覆盖为随机长字符串）
    jwt_secret: str = ""
    jwt_ttl_seconds: int = 3600  # 1 小时

    # API Key 加密密钥（AES-GCM，独立于 JWT）
    app_secret: str = ""

    # 数据库
    database_url: str = "sqlite:///./app.db"

    # 素材目录
    materials_dir: str = "./materials"

    # ---- 字幕生成（faster-whisper）----
    # 模型尺寸：tiny/base/small/medium/large-v3。
    # Demo 验证阶段默认 small（约 480MB）；中文质量不够时改 medium（约 1.5GB），无需改代码。
    whisper_model_size: str = "small"
    # 推理设备：cuda（默认，优先用 GPU）/ cpu（显式降级）/ auto（能用 GPU 就用，否则静默回 CPU）
    # RTX 4070 + CUDA 12 下 small 模型转写 60s 音频约 2~3 秒，比 CPU int8 快 6~7 倍。
    # ⚠️ 默认 **不是** auto：auto 只靠 `get_cuda_device_count()` 判断，
    # 它只说明"驱动报告有设备"，**不保证 CUDA 运行库（cublas64_12.dll 等）可加载**。
    # 本机就踩过：device_count=1 但缺 cublas，结果推理时静默挂起而不是报错。
    # 显式 cuda 现在会做**真实可用性探测**，不可用即 fail fast 并报出缺失依赖；
    # 需要降级时设环境变量 `WHISPER_DEVICE=cpu`（不改动本文件）。
    whisper_device: str = "cuda"
    # 计算精度：default（GPU 用 float16，CPU 用 int8）/ int8 / float16 / float32
    whisper_compute_type: str = "default"
    # 转写语言：zh 表示中文；空字符串表示让模型自动检测
    whisper_language: str = "zh"

    # 项目共享知识资料（与课程素材目录隔离，避免被课程扫描器误识别）
    project_context_dir: str = "./project_context"

    # 原生 video 标签使用的课程绑定播放凭证有效期
    media_ticket_ttl_seconds: int = 21_600  # 6 小时


settings = Settings()
