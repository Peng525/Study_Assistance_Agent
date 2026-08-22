"""应用配置模块。

从项目根目录 .env 读取配置，通过 pydantic-settings 管理。
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（backend/ 的上一级），.env 位于根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


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

    # 数据库
    database_url: str = "sqlite:///./app.db"

    # 素材目录
    materials_dir: str = "./materials"


settings = Settings()
