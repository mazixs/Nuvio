"""Рабочая копия cookie-файла для yt-dlp.

yt-dlp сохраняет cookie-jar обратно в файл, указанный в `cookiefile`, поэтому
платформа может удалить cookie из файла своим же ответом. Проверено на проде:
один прогон YouTube выкинул `YSC`, файл похудел с 15 записей до 14 (у TikTok и
Instagram на тех же прогонах не пропадало ничего). Накопительно от набора,
загруженного админом, осталась одна auth-cookie из шести, и YouTube начал
требовать подтверждение «я не бот».

Поэтому yt-dlp работает с копией, а загруженный оригинал остаётся нетронутым.
Копия живёт в `DATA_DIR` и переживает перезапуск: cookies, которые платформа
обновила в ответах, нужно сохранять между запусками, иначе сессия стареет
быстрее, чем платформа её продлевает.
"""

from __future__ import annotations

import os
import hashlib
import shutil
import time
from pathlib import Path

from utils.logger import setup_logger


__all__ = ["WORK_DIR_NAME", "working_cookie_file", "cleanup_old_workfiles"]

logger = setup_logger(__name__)

WORK_DIR_NAME = "cookie-work"


def _default_work_dir() -> Path:
    data_dir = os.environ.get("DATA_DIR") or str(Path(__file__).resolve().parent.parent)
    return Path(data_dir) / WORK_DIR_NAME


def working_cookie_file(
    original: Path | str | None, *, work_dir: Path | None = None
) -> Path | None:
    """Возвращает путь к копии cookie-файла, которую можно отдать yt-dlp.

    Имя копии содержит отпечаток исходного файла. Новый загруженный набор
    получает другой путь даже при совпавшем времени изменения; уже работающая
    загрузка продолжает использовать старую копию. Для прежнего набора копия
    сохраняет cookies, которые платформа успела обновить.

    Args:
        original: Путь к загруженному админом файлу.
        work_dir: Каталог для копий. По умолчанию — подкаталог в `DATA_DIR`.

    Returns:
        Путь к рабочей копии либо ``None``, если оригинала нет.
    """
    if not original:
        return None

    source = Path(original)
    if not source.is_file():
        return None

    target_dir = work_dir or _default_work_dir()
    try:
        with source.open("rb") as handle:
            fingerprint = hashlib.file_digest(handle, "sha256").hexdigest()[:16]
        target = target_dir / f"{source.stem}-{fingerprint}{source.suffix}"
        target_dir.mkdir(parents=True, exist_ok=True)
        if not target.is_file():
            shutil.copy2(source, target)
            # В файле живая сессия аккаунта, а `DATA_DIR` смонтирован ещё и в
            # контейнер WebUI — режим сужаем принудительно, не наследуя от
            # оригинала.
            target.chmod(0o600)
            logger.info("Рабочая копия cookies обновлена из оригинала: %s", target)
        # Время изменения рабочей копии также отмечает ее последнее использование.
        # Старую копию после замены исходного набора нельзя удалять посреди загрузки.
        target.touch()
    except OSError as e:
        # Без копии лучше работать по оригиналу, чем не работать вовсе:
        # деградация cookies неприятна, а отказ в скачивании — заметнее.
        logger.warning(
            "Не удалось подготовить рабочую копию cookies (%s), "
            "используется оригинал: %s",
            e,
            source,
        )
        return source

    return target


def cleanup_old_workfiles(max_age_seconds: int = 24 * 60 * 60) -> tuple[int, int]:
    """Удаляет старые копии замененных наборов, сохраняя действующие cookies."""
    from config import (
        INSTAGRAM_COOKIES_PATH,
        TIKTOK_COOKIES_PATH,
        YOUTUBE_COOKIES_PATH,
    )

    root = _default_work_dir()
    if not root.exists():
        return 0, 0
    current_names: set[str] = set()
    unreadable_prefixes: set[str] = set()
    for source in (YOUTUBE_COOKIES_PATH, INSTAGRAM_COOKIES_PATH, TIKTOK_COOKIES_PATH):
        if not source.is_file():
            continue
        try:
            with source.open("rb") as handle:
                fingerprint = hashlib.file_digest(handle, "sha256").hexdigest()[:16]
            current_names.add(f"{source.stem}-{fingerprint}{source.suffix}")
        except OSError as exc:
            logger.warning("Не удалось проверить исходные cookies %s: %s", source, exc)
            unreadable_prefixes.add(f"{source.stem}-")

    removed = failed = 0
    cutoff = time.time() - max_age_seconds
    for path in root.iterdir():
        try:
            if (
                not path.is_file()
                or path.name in current_names
                or any(path.name.startswith(prefix) for prefix in unreadable_prefixes)
                or path.stat().st_mtime >= cutoff
            ):
                continue
            path.unlink()
            removed += 1
        except OSError as exc:
            logger.warning("Не удалось удалить старую копию cookies %s: %s", path, exc)
            failed += 1
    return removed, failed
