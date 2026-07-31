"""共创智能体平台应用配置"""
import os
import secrets
import sys
from pathlib import Path
from typing import List, Literal, Optional, Self

from dotenv import dotenv_values, load_dotenv
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


def get_runtime_root() -> Path:
    """返回运行时根目录"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


RUNTIME_ROOT = get_runtime_root()


def load_env_files() -> None:
    """加载 .env 配置"""
    env_example_path = RUNTIME_ROOT / ".env.example"
    env_path = RUNTIME_ROOT / ".env"
    external_env_keys = set(os.environ.keys())

    if env_example_path.exists():
        load_dotenv(env_example_path, override=False)

    if env_path.exists():
        for key, value in dotenv_values(env_path).items():
            if value is None or key in external_env_keys:
                continue
            os.environ[key] = value


load_env_files()


def normalize_database_url(database_url: str) -> str:
    """将 PostgreSQL 默认方言规范为项目使用的 psycopg3 驱动。"""
    try:
        url = make_url(database_url)
    except ArgumentError:
        return database_url

    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
        return url.render_as_string(hide_password=False)
    return database_url


class Settings(BaseSettings):
    """应用配置"""

    model_config = SettingsConfigDict(case_sensitive=True, extra="ignore")

    # 基础配置
    PROJECT_NAME: str = "共创智能体平台 API"
    VERSION: str = "1.0.0"
    API_V1_PREFIX: str = "/api/v1"
    DEBUG: bool = True
    ENVIRONMENT: str = "development"

    @field_validator("DEBUG", mode="before")
    @classmethod
    def parse_debug_value(cls, value: object) -> object:
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on", "debug", "dev", "development"}:
                return True
            if normalized in {"0", "false", "no", "off", "release", "prod", "production"}:
                return False
        return value

    # 服务器配置
    HOST: str = "0.0.0.0"
    PORT: int = 8001

    # CORS 配置
    ALLOWED_ORIGINS_STR: str = (
        "http://localhost:5174,"
        "http://127.0.0.1:5174,"
        "http://localhost:5173,"
        "http://127.0.0.1:5173,"
        "http://localhost:3000,"
        "http://127.0.0.1:3000"
    )

    @property
    def ALLOWED_ORIGINS(self) -> List[str]:
        origins = [origin.strip() for origin in self.ALLOWED_ORIGINS_STR.split(",") if origin.strip()]
        if "*" in origins:
            return ["*"]
        return origins

    # JWT 配置（独立密钥）
    JWT_SECRET_KEY: str = "change-me-to-a-strong-random-secret-at-least-32-chars"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    # 数据库配置
    DATABASE_URL: str = "sqlite:///./cocreation.db"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_RECYCLE: int = 3600
    DB_POOL_PRE_PING: bool = True
    ASSET_CHUNK_SIZE_BYTES: int = 4 * 1024 * 1024
    ASSET_UPLOAD_OVERHEAD_MAX_BYTES: int = Field(default=1024 * 1024, gt=0)
    ASSET_METADATA_MAX_BYTES: int = Field(default=64 * 1024, gt=0)
    JSON_REQUEST_MAX_BYTES: int = Field(default=2 * 1024 * 1024, gt=0)
    FORGECAD_IMPORT_MAX_BYTES: int = Field(default=50 * 1024 * 1024, gt=0)
    CAD_PROVIDER: str = ""

    # 知识库（Milvus + V8 数据同步）
    KNOWLEDGE_MILVUS_URI: str = "http://milvus:19530"
    KNOWLEDGE_COLLECTION: str = "cocreation_knowledge"
    KNOWLEDGE_EMBEDDING_MODEL: str = "text-embedding-v4"
    KNOWLEDGE_EMBEDDING_DIM: int = 1024
    VECTOR_DB_EMBEDDING_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    KNOWLEDGE_SYNC_SOURCE_URL: Optional[str] = None   # V8 MySQL 地址（如 cygxpt-mysql:3306）
    KNOWLEDGE_SYNC_SOURCE_DB: str = "cygxszpt"
    KNOWLEDGE_SYNC_SOURCE_USER: str = "root"
    KNOWLEDGE_SYNC_SOURCE_PASSWORD: Optional[str] = None
    ENABLE_TRUSTED_MEDIA_PIPELINE: bool = False

    # 会话配置
    SESSION_COOKIE_NAME: str = "cocreation_session"
    SESSION_COOKIE_SECURE: bool = False
    SESSION_COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"
    SESSION_TTL_MINUTES: int = 1440
    ENABLE_LOCAL_TEST_USERS: bool = False
    SSO_STATE_COOKIE_NAME: str = "cocreation_sso_state"
    SSO_STATE_COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"
    SSO_STATE_TTL_MINUTES: int = Field(default=10, gt=0)
    SSO_STATE_RETENTION_MINUTES: int = Field(default=60, gt=0)
    SSO_STATE_RATE_LIMIT_WINDOW_SECONDS: int = Field(default=60, gt=0)
    SSO_STATE_RATE_LIMIT_MAX: int = Field(default=10, gt=0)
    SSO_STATE_MAX_ACTIVE_PER_BINDING: int = Field(default=5, gt=0)
    SESSION_LAST_SEEN_TOUCH_SECONDS: int = Field(default=300, gt=0)
    WORKFLOW_LEASE_SECONDS: int = 120

    @model_validator(mode="after")
    def validate_production_database(self) -> Self:
        self.DATABASE_URL = normalize_database_url(self.DATABASE_URL)
        if "*" in self.ALLOWED_ORIGINS:
            raise ValueError("ALLOWED_ORIGINS 禁止使用通配符")
        if self.SESSION_COOKIE_SAMESITE == "none" and not self.SESSION_COOKIE_SECURE:
            raise ValueError("SameSite=None 必须启用 SESSION_COOKIE_SECURE")
        if self.SSO_STATE_COOKIE_SAMESITE == "none" and not self.SESSION_COOKIE_SECURE:
            raise ValueError("SSO state SameSite=None 必须启用 SESSION_COOKIE_SECURE")
        if self.ENVIRONMENT.lower() not in {"production", "prod"}:
            return self
        if not self.SESSION_COOKIE_SECURE:
            raise ValueError("生产环境必须启用 SESSION_COOKIE_SECURE")
        # if self.ENABLE_LOCAL_TEST_USERS:
        #     raise ValueError("生产环境必须禁用 ENABLE_LOCAL_TEST_USERS")

        unsafe_jwt_secrets = {
            "change-me-to-a-strong-random-secret-at-least-32-chars",
            "your-secret-key-change-in-production",
        }
        if self.JWT_SECRET_KEY in unsafe_jwt_secrets:
            raise ValueError("生产环境禁止使用默认 JWT_SECRET_KEY")

        try:
            driver_name = make_url(self.DATABASE_URL).drivername
        except ArgumentError as exc:
            raise ValueError("生产环境 DATABASE_URL 必须使用 PostgreSQL psycopg 驱动") from exc

        if driver_name != "postgresql+psycopg":
            raise ValueError("生产环境 DATABASE_URL 必须使用 PostgreSQL psycopg 驱动")
        return self

    # 文件上传配置
    UPLOAD_MAX_SIZE: int = 100 * 1024 * 1024
    UPLOAD_PATH: str = "uploads"

    # 日志配置
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"

    # CAD AI 统一网关
    CAD_AI_BASE_URL: Optional[str] = None
    CAD_AI_API_KEY: Optional[str] = None
    CAD_AI_TIMEOUT_SECONDS: float = 240.0

    # AI 模型配置
    DASHSCOPE_API_KEY: Optional[str] = None
    LOCAL_QWEN_BASE_URL: str = "http://127.0.0.1:58080/v1"
    LOCAL_QWEN_API_KEY: str = "local-qwen"
    LOCAL_QWEN_MODEL: str = "/data/models/Qwen3-32B-INT8"
    LOCAL_QWEN_MODELS: str = "/data/models/Qwen3-32B-INT8"
    OPENAI_API_KEY: Optional[str] = None
    # Local WAN (图像生成)
    LOCAL_WAN_BASE_URL: str = ""
    LOCAL_WAN_API_KEY: str = "local-wan"
    LOCAL_WAN_MODEL: str = "wan2.7-image"

    # 官方直连 API Key 配置
    DEEPSEEK_API_KEY: Optional[str] = None
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    QWEN_API_KEY: Optional[str] = None
    QWEN_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    DOUBAO_API_KEY: Optional[str] = None
    DOUBAO_BASE_URL: str = "https://ark.cn-beijing.volces.com/api/v3"
    GLM_API_KEY: Optional[str] = None
    GLM_BASE_URL: str = "https://open.bigmodel.cn/api/paas/v4"
    CLAUDE_API_KEY: Optional[str] = None
    CLAUDE_BASE_URL: str = "https://api.anthropic.com/v1"

    # Gemini
    GEMINI_API_KEY: Optional[str] = None
    GEMINI_BASE_URL: str = "https://generativelanguage.googleapis.com/v1"
    GEMINI_IMAGE_MODEL: str = "gemini-2.0-flash-exp"
    GEMINI_IMAGE_TIMEOUT_SECONDS: float = 120

    # Zoo / KittyCAD API
    ZOO_API_BASE_URL: str = "https://api.zoo.dev"
    ZOO_API_TOKEN: Optional[str] = None
    ZOO_API_TIMEOUT_SECONDS: float = 240.0

    # NodAPI
    NODAPI_BASE_URL: str = "https://www.nodapi.com"
    NODAPI_API_KEY: Optional[str] = None
    NODAPI_CHAT_MODEL: str = "gpt-4o-mini"
    NODAPI_CHAT_TIMEOUT_SECONDS: float = 120.0
    NODAPI_IMAGE_MODEL: str = "gpt-image-2"
    NODAPI_IMAGE_SIZE: str = "1536x1024"
    NODAPI_IMAGE_TIMEOUT_SECONDS: float = 420.0
    NODAPI_IMAGE_RETRY_COUNT: int = 1
    NODAPI_IMAGE_RETRY_DELAY_SECONDS: float = 3.0
    # NodAPI Midjourney
    NODAPI_MIDJOURNEY_PROMPT_SUFFIX: str = "--ar 3:2"
    NODAPI_MIDJOURNEY_TIMEOUT_SECONDS: float = 900.0
    NODAPI_MIDJOURNEY_POLL_INTERVAL_SECONDS: float = 5.0

    # 前端地址（SSO 回调重定向目标）
    FRONTEND_URL: str = "http://localhost:5174"

    # 主平台 SSO 配置
    MAIN_PLATFORM_URL: str = "http://localhost:8000"
    SSO_CLIENT_ID: Optional[str] = None
    SSO_CLIENT_SECRET: Optional[str] = None
    SSO_REDIRECT_URI: str = "http://localhost:8001/api/v1/auth/sso/callback"

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() in ("production", "prod")


settings = Settings()
