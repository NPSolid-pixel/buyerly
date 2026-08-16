from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_VERSION: str = Field(default="dev", description="Deployed Git commit SHA")
    BOT_TOKEN: str = Field(default="", description="Telegram Bot Token from @BotFather")
    DATABASE_URL: str = Field(default="sqlite+aiosqlite:///mediabuyer.db", description="SQLite database URL")
    DEFAULT_POLL_INTERVAL_MINUTES: int = Field(default=5, description="Monitoring interval in minutes")
    ADMIN_CHAT_ID: str = Field(default="", description="Default Telegram Chat ID for alerts")
    WEBAPP_URL: str = Field(default="", description="Public HTTPS URL for Telegram Web App")
    API_PORT: int = Field(default=8080, description="Web API and static files port")
    API_HOST: str = Field(default="0.0.0.0", description="Web API host")
    ENABLE_DEV_AUTH: bool = Field(default=False, description="Enable dev auth fallback for local tests")
    TELEGRAM_INIT_DATA_MAX_AGE_SECONDS: int = Field(
        default=86400,
        ge=60,
        description="Maximum accepted age of Telegram Mini App initData",
    )
    BOOTSTRAP_ADMIN_USERNAME: str = Field(
        default="",
        description="Optional first admin username for an empty installation",
    )
    BOOTSTRAP_ADMIN_PASSWORD: str = Field(
        default="",
        description="Optional first admin password for an empty installation",
    )
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
