"""Application configuration loaded from environment variables."""

from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Application settings."""

    telegram_bot_token: str = ""
    database_url: str = f"sqlite+aiosqlite:///{BASE_DIR / 'data' / 'app.db'}"
    app_timezone: str = "Europe/Berlin"

    # LLM provider settings
    llm_provider: str = "mock"  # mock | openai_compatible
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""

    # Vision provider settings
    vision_provider: str = "mock"  # mock | openai_compatible
    vision_api_key: str = ""
    vision_base_url: str = ""
    vision_model: str = ""

    @property
    def data_dir(self) -> Path:
        return BASE_DIR / "data"

    @property
    def photos_dir(self) -> Path:
        return self.data_dir / "photos"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
