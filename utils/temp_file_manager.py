"""
Модуль для управления временными файлами.
"""

import shutil
import time
import uuid
from pathlib import Path
from config import TEMP_DIR
from utils.logger import setup_logger

logger = setup_logger(__name__)


def create_temp_dir(session_id: str | None = None) -> Path:
    """
    Создаёт временную директорию для пользовательской сессии.

    Args:
        session_id (str, optional): Идентификатор сессии. Если не указан,
                                    создается случайный идентификатор.

    Returns:
        Path: Путь к созданной временной директории
    """
    if session_id is None:
        session_id = str(uuid.uuid4())

    temp_path = TEMP_DIR / session_id
    temp_path.mkdir(parents=True, exist_ok=True)

    logger.debug(f"Создана временная директория: {temp_path}")
    return temp_path


def get_temp_file_path(session_id: str, filename: str) -> Path:
    """
    Получает путь к временному файлу.

    Args:
        session_id (str): Идентификатор сессии.
        filename (str): Имя файла.

    Returns:
        Path: Путь к временному файлу
    """
    temp_path = TEMP_DIR / session_id
    temp_path.mkdir(parents=True, exist_ok=True)
    return temp_path / filename


def cleanup_temp_files(session_id=None):
    """
    Очищает временные файлы.

    Args:
        session_id (str, optional): Идентификатор сессии. Если не указан,
                                    очищаются все временные файлы.
    """
    removed = 0
    failed = 0
    try:
        targets = (
            [TEMP_DIR / session_id]
            if session_id
            else list(TEMP_DIR.iterdir()) if TEMP_DIR.exists() else []
        )
    except OSError as exc:
        logger.error("Не удалось прочитать каталог временных файлов: %s", exc)
        return 0, 1
    for item in targets:
        try:
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
            removed += 1
        except FileNotFoundError:
            continue
        except OSError as exc:
            failed += 1
            logger.error("Не удалось удалить временный файл %s: %s", item, exc)
    logger.info("Очистка временных файлов: удалено %s, ошибок %s", removed, failed)
    return removed, failed


def cleanup_stale_temp_files(
    max_age_seconds: int = 24 * 60 * 60, *, active_sessions: set[str] | None = None
) -> tuple[int, int]:
    """Удаляет брошенные файлы, не трогая текущие загрузки."""
    if not TEMP_DIR.exists():
        return 0, 0
    cutoff = time.time() - max_age_seconds
    active = active_sessions or set()
    removed = failed = 0
    for item in TEMP_DIR.iterdir():
        if item.name in active:
            continue
        try:
            if item.stat().st_mtime >= cutoff:
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
            removed += 1
        except FileNotFoundError:
            continue
        except OSError as exc:
            failed += 1
            logger.error("Не удалось удалить старый временный файл %s: %s", item, exc)
    return removed, failed
