from functools import lru_cache
from pathlib import Path
from typing import List

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOTDIR = Path(__file__).parent
TMPDIR = ROOTDIR / "tmp"
PDF_TMP_PATH = TMPDIR / "downloaded.pdf"
PROMPT_DIR = ROOTDIR / "prompts"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Server Configuration
    HOST: str = "0.0.0.0"
    PORT: int = 8001
    DEBUG: bool = False
    LOG_LEVEL: str = "info"

    # CORS Configuration
    CORS_ORIGINS: str = "*"

    # Anthropic Configuration
    ANTHROPIC_API_KEY: str
    ANTHROPIC_MODEL: str = "claude-sonnet-4-5-20250929"
    MAX_TOKENS: int = 4096

    # ScrapingBee Configuration
    SCRAPINGBEE_API_KEY: str

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]

@lru_cache()
def get_settings() -> Settings:
    load_dotenv()
    return Settings()  # type: ignore[call-arg]

settings = get_settings()

