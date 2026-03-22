"""
Утилиты для headless-эксплуатации yt-dlp: версия, автообновление, CLI-запуск.
"""

from __future__ import annotations

import importlib.metadata
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from config import (
    YTDLP_AUTO_UPDATE,
    YTDLP_AUTO_UPDATE_TIMEOUT,
    YTDLP_CLI_TIMEOUT,
    YTDLP_RELEASE_CHANNEL,
)
from utils.logger import setup_logger

logger = setup_logger(__name__)


@dataclass(frozen=True)
class YtDlpUpdateResult:
    attempted: bool
    succeeded: bool
    channel: str
    command: tuple[str, ...]
    version_before: str | None
    version_after: str | None
    stdout: str = ""
    stderr: str = ""


def get_installed_yt_dlp_version() -> str | None:
    """Возвращает установленную версию yt-dlp без импорта самого пакета."""
    try:
        return importlib.metadata.version("yt-dlp")
    except importlib.metadata.PackageNotFoundError:
        return None


def build_yt_dlp_upgrade_command(channel: str | None = None) -> list[str]:
    """Собирает headless-safe команду обновления yt-dlp."""
    selected_channel = (channel or YTDLP_RELEASE_CHANNEL).strip().lower()
    base_command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--upgrade",
    ]

    if selected_channel == "stable":
        return [*base_command, "yt-dlp[default]"]

    if selected_channel == "master":
        return [
            *base_command,
            "--force-reinstall",
            "yt-dlp[default] @ https://github.com/yt-dlp/yt-dlp/archive/master.tar.gz",
        ]

    return [*base_command, "--pre", "yt-dlp[default]"]


def ensure_latest_yt_dlp(
    reason: str = "startup",
    *,
    force: bool = False,
    timeout: int | None = None,
) -> YtDlpUpdateResult:
    """Best-effort обновление yt-dlp. Не использует GUI и не роняет сервис."""
    if not YTDLP_AUTO_UPDATE and not force:
        return YtDlpUpdateResult(
            attempted=False,
            succeeded=True,
            channel=YTDLP_RELEASE_CHANNEL,
            command=tuple(),
            version_before=get_installed_yt_dlp_version(),
            version_after=get_installed_yt_dlp_version(),
        )

    version_before = get_installed_yt_dlp_version()
    command = build_yt_dlp_upgrade_command()
    logger.info(
        "Проверка/обновление yt-dlp перед запуском (%s), канал=%s, версия до=%s",
        reason,
        YTDLP_RELEASE_CHANNEL,
        version_before or "not-installed",
    )
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout or YTDLP_AUTO_UPDATE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Не удалось запустить обновление yt-dlp: %s", exc)
        return YtDlpUpdateResult(
            attempted=True,
            succeeded=False,
            channel=YTDLP_RELEASE_CHANNEL,
            command=tuple(command),
            version_before=version_before,
            version_after=version_before,
            stderr=str(exc),
        )

    version_after = get_installed_yt_dlp_version()
    if completed.returncode == 0:
        logger.info(
            "yt-dlp готов к работе: %s -> %s",
            version_before or "not-installed",
            version_after or "not-installed",
        )
        return YtDlpUpdateResult(
            attempted=True,
            succeeded=True,
            channel=YTDLP_RELEASE_CHANNEL,
            command=tuple(command),
            version_before=version_before,
            version_after=version_after,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    logger.warning(
        "Обновление yt-dlp завершилось с кодом %s. Продолжаем на локально доступной версии %s. stderr=%s",
        completed.returncode,
        version_before or "not-installed",
        completed.stderr.strip()[:500],
    )
    return YtDlpUpdateResult(
        attempted=True,
        succeeded=False,
        channel=YTDLP_RELEASE_CHANNEL,
        command=tuple(command),
        version_before=version_before,
        version_after=version_after or version_before,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def run_yt_dlp_cli(command: list[str], *, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    """Запускает локальный `python -m yt_dlp` сценарий без GUI."""
    logger.info("CLI fallback yt-dlp: %s", " ".join(command))
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout or YTDLP_CLI_TIMEOUT,
        check=False,
    )


def extract_cli_output_path(stdout: str) -> Path | None:
    """Возвращает последний путь, напечатанный `--print after_move:filepath`."""
    for raw_line in reversed(stdout.splitlines()):
        candidate = raw_line.strip().strip('"')
        if not candidate:
            continue
        path = Path(candidate)
        if path.exists():
            return path
    return None
