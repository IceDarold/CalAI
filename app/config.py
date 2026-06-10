"""Application configuration loaded from environment variables."""

from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Application settings."""

    telegram_bot_token: str = ""
    telegram_proxy: str = ""  # SOCKS5 or HTTP proxy URL for Telegram API (e.g. socks5://127.0.0.1:3128)
    database_url: str = f"sqlite+aiosqlite:///{BASE_DIR / 'data' / 'app.db'}"
    app_timezone: str = "Europe/Berlin"

    # AI provider selection: "yandex" | "openai" | "mock"
    ai_provider: str = "mock"

    # YandexGPT
    yandex_api_key: str = ""
    yandex_folder_id: str = ""
    yandex_model: str = "yandexgpt-5.1"

    # OpenAI-compatible (used when ai_provider = "openai")
    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_model: str = ""

    @property
    def is_ai_configured(self) -> bool:
        """Check if any real AI provider is configured."""
        if self.ai_provider == "yandex":
            return bool(self.yandex_api_key and self.yandex_folder_id)
        if self.ai_provider == "openai":
            return bool(self.openai_api_key)
        return False

    @property
    def data_dir(self) -> Path:
        return BASE_DIR / "data"

    @property
    def photos_dir(self) -> Path:
        return self.data_dir / "photos"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
