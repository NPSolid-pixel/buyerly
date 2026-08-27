from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_VERSION: str = Field(default="dev", description="Deployed Git commit SHA")
    BOT_TOKEN: str = Field(default="", description="Telegram Bot Token from @BotFather")
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://buyerly:buyerly_secret@localhost:5432/buyerly",
        description="Async SQLAlchemy database URL",
    )
    DEFAULT_POLL_INTERVAL_MINUTES: int = Field(default=5, description="Monitoring interval in minutes")
    ADMIN_CHAT_ID: str = Field(default="", description="Default Telegram Chat ID for alerts")
    WEBAPP_URL: str = Field(default="", description="Public HTTPS URL for Telegram Web App")
    META_GRAPH_VERSION: str = Field(
        default="v26.0",
        description="Pinned Meta Graph API version (for example v26.0)",
    )
    META_APP_ID: str = Field(default="", description="Meta application ID")
    META_APP_SECRET: str = Field(default="", description="Meta application secret")
    META_LOGIN_CONFIG_ID: str = Field(
        default="",
        description="Facebook Login for Business configuration ID",
    )
    META_OAUTH_REDIRECT_URI: str = Field(
        default="",
        description="Exact HTTPS callback registered in Meta",
    )
    META_TOKEN_ENCRYPTION_KEY: str = Field(
        default="",
        description="Primary URL-safe base64 key followed by optional decrypt-only rotation keys",
    )
    API_PORT: int = Field(default=8080, description="Web API and static files port")
    API_HOST: str = Field(default="0.0.0.0", description="Web API host")
    SERVE_STATIC: bool = Field(default=True, description="Serve the bundled web app from FastAPI in local/legacy mode")
    ENABLE_DEV_AUTH: bool = Field(default=False, description="Enable dev auth fallback for local tests")
    CORS_ORIGINS: str = Field(
        default="",
        description="Comma-separated list of allowed CORS origins (e.g. https://buyerly.app)",
    )
    TELEGRAM_INIT_DATA_MAX_AGE_SECONDS: int = Field(
        default=86400,
        ge=60,
        description="Maximum accepted age of Telegram Mini App initData",
    )
    WEB_SESSION_TTL_HOURS: int = Field(
        default=24,
        ge=1,
        le=168,
        description="Absolute lifetime of a browser session",
    )
    WEB_SESSION_ROTATE_MINUTES: int = Field(
        default=15,
        ge=1,
        le=1440,
        description="Maximum age of a browser session token before rotation",
    )
    SESSION_COOKIE_SECURE: bool = Field(
        default=True,
        description="Send browser authentication cookies only over HTTPS",
    )
    BOOTSTRAP_ADMIN_USERNAME: str = Field(
        default="",
        description="Optional first admin username for an empty installation",
    )
    BOOTSTRAP_ADMIN_PASSWORD: str = Field(
        default="",
        description="Optional first admin password for an empty installation",
    )
    RESEND_API_KEY: str = Field(
        default="",
        description="Resend.com API Key for sending transactional emails",
    )
    EMAIL_FROM: str = Field(
        default="Buyerly <team@buyerly.app>",
        description="Default sender email header for transactional emails",
    )
    SMTP_HOST: str = Field(default="", description="Optional SMTP host")
    SMTP_PORT: int = Field(default=587, description="Optional SMTP port")
    SMTP_USER: str = Field(default="", description="Optional SMTP user")
    SMTP_PASSWORD: str = Field(default="", description="Optional SMTP password")
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
