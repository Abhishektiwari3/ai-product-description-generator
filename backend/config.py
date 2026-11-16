from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    default_provider: str = os.getenv("DEFAULT_PROVIDER", "auto")

    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_text_model: str = os.getenv("OPENAI_TEXT_MODEL", "gpt-4o-mini")
    openai_image_model: str = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2")

    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_text_model: str = os.getenv("GEMINI_TEXT_MODEL", "gemini-3.5-flash")
    gemini_image_model: str = os.getenv("GEMINI_IMAGE_MODEL", "gemini-nano-banana-2.1")

    request_timeout_s: float = float(os.getenv("REQUEST_TIMEOUT_S", "60"))
    max_retries: int = int(os.getenv("MAX_RETRIES", "2"))

    rate_limit_per_minute: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "30"))
    max_image_mb: float = float(os.getenv("MAX_IMAGE_MB", "8"))

    mongo_uri: str = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    mongo_db: str = os.getenv("MONGO_DB", "product_copy_ai")
    allow_memory_fallback: bool = _bool("ALLOW_MEMORY_FALLBACK", True)

    store_inputs: bool = _bool("STORE_INPUTS", True)

    allowed_origins: list = field(
        default_factory=lambda: [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
    )

    def resolve_provider(self, requested: Optional[str]) -> str:
        """Pick the provider for one request. 'auto' prefers a configured real provider."""
        p = (requested or "auto").lower()
        if p == "auto":
            p = (self.default_provider or "auto").lower()
        if p == "auto":
            if self.openai_api_key:
                return "openai"
            if self.gemini_api_key:
                return "gemini"
            return "mock"
        return p

    def available_providers(self) -> dict:
        return {
            "mock": True,
            "openai": bool(self.openai_api_key),
            "gemini": bool(self.gemini_api_key),
        }


settings = Settings()
