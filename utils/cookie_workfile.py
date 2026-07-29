"""Рабочая копия cookie-файла для yt-dlp.

yt-dlp сохраняет cookie-jar обратно в файл, указанный в `cookiefile`, и при
сохранении теряет сессионные cookies. Проверено на проде: один прогон выкинул
`YSC`, файл похудел с 15 записей до 14. Накопительно от набора, загруженного
админом, осталась одна auth-cookie из шести, и YouTube начал требовать
подтверждение «я не бот».

Поэтому yt-dlp работает с копией, а загруженный оригинал остаётся нетронутым.
Копия живёт в `DATA_DIR` и переживает перезапуск: cookies, которые платформа
обновила в ответах, нужно сохранять между запусками, иначе сессия стареет
быстрее, чем платформа её продлевает.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from utils.logger import setup_logger


__all__ = ["WORK_DIR_NAME", "working_cookie_file"]

logger = setup_logger(__name__)

WORK_DIR_NAME = "cookie-work"


def _default_work_dir() -> Path:
    data_dir = os.environ.get("DATA_DIR") or str(Path(__file__).resolve().parent.parent)
    return Path(data_dir) / WORK_DIR_NAME


def working_cookie_file(
    original: Path | str | None, *, work_dir: Path | None = None
) -> Path | None:
    """Возвращает путь к копии cookie-файла, которую можно отдать yt-dlp.

    Копия обновляется из оригинала только когда оригинал новее — то есть когда
    админ загрузил свежий набор. В остальных случаях возвращается уже
    существующая копия со всеми cookies, которые платформа успела в ней
    обновить.

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
    target = target_dir / source.name

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        if not target.is_file() or source.stat().st_mtime > target.stat().st_mtime:
            shutil.copy2(source, target)
            # В файле живая сессия аккаунта, а `DATA_DIR` смонтирован ещё и в
            # контейнер WebUI — режим сужаем принудительно, не наследуя от
            # оригинала.
            target.chmod(0o600)
            logger.info("Рабочая копия cookies обновлена из оригинала: %s", target)
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
