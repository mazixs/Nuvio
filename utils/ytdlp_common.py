"""
Shared utilities and configuration for yt-dlp downloaders.
"""

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yt_dlp
from config import MAX_FILE_SIZE
from utils.cancellation import cancellation_hook
from utils.logger import setup_logger

logger = setup_logger(__name__)

DEFAULT_YTDLP_NETWORK_OPTS: dict[str, Any] = {
    "retries": 5,
    "socket_timeout": 40,
    "http_chunk_size": 10_485_760,  # 10 MB
    "fragment_retries": 5,
    "skip_unavailable_fragments": True,
    "abort_on_unavailable_fragments": False,
    "concurrent_fragment_downloads": 4,
    "continuedl": False,
    "noplaylist": True,
    "remote_components": ["ejs:github"],
}

_NETWORK_TIMEOUT_SIGNATURES = (
    "Read timed out",
    "Connection timed out",
    "Timed out",
    "Connection reset by peer",
    "UNEXPECTED_EOF_WHILE_READING",
    "EOF occurred in violation of protocol",
    "fragment not found",
    "HTTP Error 403",
)


def classify_download_error_kind(message: str) -> str:
    """Classifies the type of DownloadError for correct logging level and flow control."""
    msg_lower = message.lower()
    if "requested format is not available" in msg_lower:
        return "FORMAT_UNAVAILABLE"
    if any(
        signature in msg_lower
        for signature in (
            "http error 403",
            "forbidden",
            "login required",
            "private video",
        )
    ):
        return "ACCESS_RESTRICTED"
    if any(
        signature in msg_lower
        for signature in (
            "requires a javascript runtime",
            "nsig extraction failed",
            "signature extraction failed",
            "unable to extract initial player response",
            "remote components",
        )
    ):
        return "EXTRACTOR_RUNTIME"
    if any(signature.lower() in msg_lower for signature in _NETWORK_TIMEOUT_SIGNATURES):
        return "NETWORK_TIMEOUT"
    return "UNKNOWN"


def apply_network_opts(options: dict[str, Any], session_id: str | None = None) -> None:
    """Applies default network options to yt-dlp config dict.

    Args:
        session_id: сессия, чья отмена должна прерывать загрузку. Без него
            хук не ставится: разбор информации о видео идёт до появления
            сессии и отменять там нечего.
    """
    options.update(DEFAULT_YTDLP_NETWORK_OPTS)
    if session_id:
        hooks = list(options.get("progress_hooks") or [])
        hooks.append(cancellation_hook(session_id))
        options["progress_hooks"] = hooks


def execute_with_backoff(
    description: str, func: Callable[[], Path | str], max_attempts: int = 3
) -> Path | str:
    """Executes a downloader function with exponential backoff on network timeouts."""
    for attempt in range(1, max_attempts + 1):
        try:
            return func()
        except yt_dlp.utils.DownloadError as e:
            message = str(e)
            error_kind = classify_download_error_kind(message)
            if error_kind == "NETWORK_TIMEOUT":
                if attempt == max_attempts:
                    logger.error(
                        "%s failed after %s attempts due to timeout: %s",
                        description,
                        attempt,
                        message,
                        exc_info=True,
                    )
                    raise
                delay = min(2**attempt, 30)
                logger.warning(
                    "%s: timeout (attempt %s/%s). Retrying in %ss",
                    description,
                    attempt,
                    max_attempts,
                    delay,
                )
                time.sleep(delay)
                continue
            if error_kind in {"FORMAT_UNAVAILABLE", "ACCESS_RESTRICTED"}:
                logger.warning(
                    "%s: expected yt-dlp error (%s): %s",
                    description,
                    error_kind,
                    message,
                )
            else:
                logger.error(
                    "%s: download error: %s", description, message, exc_info=True
                )
            raise


class FileSizeLimitError(Exception):
    """Файл превышает предел выбранного Telegram Bot API."""


def finalize_downloaded_file(downloaded_file: Path, force_local: bool) -> Path:
    """Возвращает локальный файл или удаляет его при превышении лимита."""
    file_size = downloaded_file.stat().st_size
    if force_local or file_size <= MAX_FILE_SIZE:
        return downloaded_file

    logger.warning(
        "Файл %s (%s байт) превышает лимит Telegram %s байт.",
        downloaded_file,
        file_size,
        MAX_FILE_SIZE,
    )
    downloaded_file.unlink(missing_ok=True)
    raise FileSizeLimitError(
        f"Файл превышает допустимый размер: {file_size} > {MAX_FILE_SIZE}"
    )
