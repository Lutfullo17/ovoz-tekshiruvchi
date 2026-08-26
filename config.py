"""Sozlamalar — barchasi .env orqali boshqariladi."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _tashkent_tz():
    """Asia/Tashkent. tzdata bo'lmasa UTC+5 (O'zbekistonda DST yo'q)."""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("Asia/Tashkent")
    except Exception:
        return timezone(timedelta(hours=5), "UTC+5")


TASHKENT = _tashkent_tz()


def _load_dotenv(path: Path) -> None:
    """Minimal .env o'qish (tashqi kutubxonasiz)."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int((os.environ.get(name) or "").strip() or default)
    except ValueError:
        return default


def _str(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


@dataclass(frozen=True)
class Config:
    # --- Telegram ---
    bot_token: str = field(default_factory=lambda: _str("BOT_TOKEN"))
    chat_id: str = field(default_factory=lambda: _str("CHAT_ID"))

    # --- Groq ---
    groq_api_key: str = field(default_factory=lambda: _str("GROQ_API_KEY"))
    groq_model: str = field(default_factory=lambda: _str("GROQ_MODEL", "qwen/qwen3.8-27b"))

    # --- Kuzatiladigan loyiha ---
    # DIQQAT: "Quyi Tegana ... H.Boyaqaro va Bobir ko'chalarini asfaltlash"
    # loyihasining haqiqiy publicId si — 055530954008 (saytdan tekshirilgan).
    project_id: str = field(default_factory=lambda: _str("PROJECT_ID", "055530954008"))

    # --- Manba (Ochiq budjet) ---
    board_id: int = field(default_factory=lambda: _int("BOARD_ID", 55))
    region_id: int = field(default_factory=lambda: _int("REGION_ID", 8))       # Samarqand
    district_id: int = field(default_factory=lambda: _int("DISTRICT_ID", 93))  # Urgut
    district_label: str = field(default_factory=lambda: _str("DISTRICT_LABEL", "Urgut tumani"))
    stage: str = field(default_factory=lambda: _str("STAGE", "PASSED"))

    # --- Jadval ---
    interval_minutes: int = field(default_factory=lambda: _int("INTERVAL_MINUTES", 30))
    quiet_start: int = field(default_factory=lambda: _int("QUIET_START", 1))  # 01:00
    quiet_end: int = field(default_factory=lambda: _int("QUIET_END", 6))      # 06:00

    # --- Hisobot ---
    top_n: int = field(default_factory=lambda: _int("TOP_N", 15))

    # --- Texnik ---
    max_retries: int = field(default_factory=lambda: _int("MAX_RETRIES", 3))
    request_timeout: int = field(default_factory=lambda: _int("REQUEST_TIMEOUT", 30))
    scraper_mode: str = field(default_factory=lambda: _str("SCRAPER_MODE", "auto").lower())
    # auto = avval API, kerak bo'lsa Playwright | api | playwright

    @property
    def db_path(self) -> Path:
        return BASE_DIR / _str("DB_PATH", "history.db")

    @property
    def log_path(self) -> Path:
        return BASE_DIR / _str("LOG_PATH", "bot.log")

    @property
    def api_base(self) -> str:
        return "https://new.openbudget.uz"

    @property
    def page_url(self) -> str:
        return f"{self.api_base}/uz/initiative-budget/active-initiatives/{self.board_id}"

    def is_quiet(self, hour: int) -> bool:
        """Jim soatmi? Yarim tundan o'tuvchi oraliqni ham qo'llab-quvvatlaydi."""
        if self.quiet_start == self.quiet_end:
            return False
        if self.quiet_start < self.quiet_end:
            return self.quiet_start <= hour < self.quiet_end
        return hour >= self.quiet_start or hour < self.quiet_end


CONFIG = Config()
