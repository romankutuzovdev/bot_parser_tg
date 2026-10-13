from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Папка рядом с exe/скриптом (важно для Windows-пакета)
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

os.chdir(BASE_DIR)
load_dotenv(BASE_DIR / ".env")


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _list(value: str | None) -> list[str]:
    if not value or not value.strip():
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "").strip()
SESSION_NAME = os.getenv("SESSION_NAME", "parser_session").strip()
DEVICE_MODEL = os.getenv("DEVICE_MODEL", "").strip() or None
SYSTEM_VERSION = os.getenv("SYSTEM_VERSION", "").strip() or None
APP_VERSION = os.getenv("APP_VERSION", "").strip() or None
LANG_CODE = os.getenv("LANG_CODE", "en").strip() or "en"
SYSTEM_LANG_CODE = os.getenv("SYSTEM_LANG_CODE", "en-US").strip() or "en-US"

KEYWORDS = [k.lower() for k in _list(os.getenv("KEYWORDS"))]
SOURCE_CHATS = _list(os.getenv("SOURCE_CHATS"))
TARGET_CHATS = _list(os.getenv("TARGET_CHAT") or os.getenv("TARGET_CHATS"))
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

SAVE_TO_FILE = _bool(os.getenv("SAVE_TO_FILE"), True)
OUTPUT_FILE = BASE_DIR / os.getenv("OUTPUT_FILE", "found_posts.jsonl")
HISTORY_LIMIT = int(os.getenv("HISTORY_LIMIT", "100"))
LISTEN_NEW = _bool(os.getenv("LISTEN_NEW"), True)


def validate() -> None:
    errors: list[str] = []
    if not API_ID:
        errors.append("API_ID")
    if not API_HASH:
        errors.append("API_HASH")
    if not KEYWORDS:
        errors.append("KEYWORDS")
    if not TARGET_CHATS and not SAVE_TO_FILE:
        errors.append("TARGET_CHAT или SAVE_TO_FILE=true")
    if TARGET_CHATS and not BOT_TOKEN:
        errors.append("BOT_TOKEN (нужен для отправки в TARGET_CHAT)")
    if errors:
        raise SystemExit(
            "Заполни в .env: " + ", ".join(errors) + "\nСкопируй .env.example → .env"
        )