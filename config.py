"""
Файл конфигурации для Telegram бота.
"""

import os
import logging
from pathlib import Path


def _parse_bool(value: str | None, default: bool = False) -> bool:
    """Парсит булевы env-значения."""
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _parse_log_level(value: str | None) -> int:
    """Возвращает числовой уровень логирования из env-строки."""
    level_name = (value or "INFO").strip().upper()
    return getattr(logging, level_name, logging.INFO)


def _parse_admin_ids(value: str | None) -> list[int]:
    """Парсит ADMIN_IDS, игнорируя пустые и некорректные значения."""
    admin_ids: list[int] = []
    for raw_value in (value or "").split(","):
        candidate = raw_value.strip()
        if not candidate:
            continue
        try:
            admin_ids.append(int(candidate))
        except ValueError:
            logging.warning("ADMIN_IDS содержит некорректное значение %r и будет проигнорирован", candidate)
    return admin_ids


def _parse_ytdlp_release_channel(value: str | None) -> str:
    """Нормализует канал обновления yt-dlp."""
    channel = (value or "nightly").strip().lower()
    if channel in {"stable", "nightly", "master"}:
        return channel
    logging.warning(
        "YTDLP_RELEASE_CHANNEL=%r не поддерживается, используем nightly",
        value,
    )
    return "nightly"

# Токен Telegram бота (получить у @BotFather)
# ОБЯЗАТЕЛЬНО: установите переменную окружения TELEGRAM_TOKEN
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")

# Настройки логирования
LOG_LEVEL = _parse_log_level(os.environ.get("LOG_LEVEL"))
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# Пути для сохранения файлов
BASE_DIR = Path(__file__).parent
TEMP_DIR = BASE_DIR / "temp"
TEMP_DIR.mkdir(exist_ok=True)
SECRETS_DIR = BASE_DIR / ".secrets"
SECRETS_DIR.mkdir(exist_ok=True)

# Ограничения
MAX_VIDEO_DURATION = 3 * 60 * 60  # 3 часа в секундах
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 МБ в байтах

# Настройки пула исполнителей для блокирующих задач
DOWNLOAD_WORKERS = int(os.environ.get("DOWNLOAD_WORKERS", "8"))
BLOCKING_TASK_TIMEOUT = int(os.environ.get("BLOCKING_TASK_TIMEOUT", "600"))  # сек

# Rolling-release стратегия для yt-dlp
YTDLP_AUTO_UPDATE = _parse_bool(os.environ.get("YTDLP_AUTO_UPDATE"), default=True)
YTDLP_RELEASE_CHANNEL = _parse_ytdlp_release_channel(
    os.environ.get("YTDLP_RELEASE_CHANNEL")
)
YTDLP_AUTO_UPDATE_TIMEOUT = int(os.environ.get("YTDLP_AUTO_UPDATE_TIMEOUT", "240"))
YTDLP_CLI_FALLBACK = _parse_bool(os.environ.get("YTDLP_CLI_FALLBACK"), default=True)
YTDLP_CLI_TIMEOUT = int(os.environ.get("YTDLP_CLI_TIMEOUT", "900"))

# Ключ для доступа к Gokapi API
# Опционально: используется только для выгрузки файлов, которые превышают лимит Telegram
GOKAPI_API_KEY = os.environ.get("GOKAPI_API_KEY")

# URL для Gokapi API (по умолчанию можно оставить пустым)
GOKAPI_BASE_URL = os.environ.get("GOKAPI_BASE_URL", "")

# Список ID администраторов (через запятую)
# Пример: ADMIN_IDS = 123456789,987654321
ADMIN_IDS = _parse_admin_ids(os.environ.get("ADMIN_IDS"))

# Валидация критически важных переменных окружения
def validate_config():
    """Проверяет наличие всех необходимых переменных окружения."""
    missing_vars = []
    gokapi_api_key = (GOKAPI_API_KEY or "").strip()
    gokapi_base_url = (GOKAPI_BASE_URL or "").strip()
    
    if not TELEGRAM_TOKEN:
        missing_vars.append("TELEGRAM_TOKEN")

    if missing_vars:
        error_msg = (
            f"Отсутствуют обязательные переменные окружения: {', '.join(missing_vars)}\n"
            "Пожалуйста, установите их перед запуском бота.\n"
            "Пример для Windows: set TELEGRAM_TOKEN=your_token_here\n"
            "Пример для Linux/Mac: export TELEGRAM_TOKEN=your_token_here"
        )
        raise ValueError(error_msg)

    if gokapi_api_key and not gokapi_base_url:
        logging.warning(
            "GOKAPI_API_KEY задан без GOKAPI_BASE_URL. Выгрузка больших файлов будет недоступна."
        )
    elif gokapi_base_url and not gokapi_api_key:
        logging.warning(
            "GOKAPI_BASE_URL задан без GOKAPI_API_KEY. Выгрузка больших файлов будет недоступна."
        )
    
    return True

def _default_secret_path(filename: str) -> Path:
    """Возвращает основной путь для приватных файлов внутри .secrets."""
    return SECRETS_DIR / filename


def resolve_secret_path(filename: str) -> Path:
    """Возвращает совместимый путь к приватному файлу.

    Предпочитает `.secrets/<filename>`, но поддерживает legacy-файл в корне проекта,
    если пользователь ещё не мигрировал локальные секреты.
    """
    preferred = _default_secret_path(filename)
    legacy = BASE_DIR / filename
    if preferred.exists():
        if legacy.exists():
            logging.warning(
                "Обнаружены canonical и legacy версии секрета %s. Используем %s и игнорируем %s",
                filename,
                preferred,
                legacy,
            )
        return preferred
    if not legacy.exists():
        return preferred
    return legacy


# Пути к файлам cookies (опционально). По умолчанию используются файлы в `.secrets/`,
# но legacy-файлы в корне проекта продолжают поддерживаться как fallback.
YOUTUBE_COOKIES_PATH = Path(
    os.environ.get("YOUTUBE_COOKIES_FILE", str(resolve_secret_path("www.youtube.com_cookies.txt")))
)
INSTAGRAM_COOKIES_PATH = Path(
    os.environ.get("INSTAGRAM_COOKIES_FILE", str(resolve_secret_path("www.instagram.com_cookies.txt")))
)
TIKTOK_COOKIES_PATH = Path(
    os.environ.get("TIKTOK_COOKIES_FILE", str(resolve_secret_path("www.tiktok.com_cookies.txt")))
)

# Строковая совместимость для youtube_utils/tests
YOUTUBE_COOKIES_FILE = str(YOUTUBE_COOKIES_PATH)

