"""Application configuration loaded from environment variables."""

from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Application settings."""

    telegram_bot_token: str = ""
    telegram_proxy: str = ""
    database_url: str = f"sqlite+aiosqlite:///{BASE_DIR / 'data' / 'app.db'}"
    app_timezone: str = "Europe/Berlin"

    # AI provider: "gigachat" | "yandex" | "openai" | "mock"
    ai_provider: str = "mock"

    # GigaChat (Sber) — recommended: text + vision in one API
    gigachat_credentials: str = ""  # base64(client_id:client_secret) or just "client_id:client_secret"
    gigachat_model: str = "GigaChat-2-Max"

    # YandexGPT (legacy, text-only)
    yandex_api_key: str = ""
    yandex_folder_id: str = ""
    yandex_model: str = "yandexgpt-5.1"

    # OpenAI-compatible
    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_model: str = ""

    @property
    def is_ai_configured(self) -> bool:
        if self.ai_provider == "gigachat":
            return bool(self.gigachat_credentials)
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
