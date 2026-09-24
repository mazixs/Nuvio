"""Версия установленного yt-dlp и локальный CLI fallback."""

from __future__ import annotations

import importlib.metadata
import subprocess
from pathlib import Path

from config import YTDLP_CLI_TIMEOUT
from utils.logger import setup_logger

logger = setup_logger(__name__)


def get_installed_yt_dlp_version() -> str | None:
    """Возвращает установленную версию yt-dlp без импорта самого пакета."""
    try:
        return importlib.metadata.version("yt-dlp")
    except importlib.metadata.PackageNotFoundError:
        return None


def run_yt_dlp_cli(
    command: list[str], *, timeout: int | None = None
) -> subprocess.CompletedProcess[str]:
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
