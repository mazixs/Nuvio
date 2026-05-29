"""
Shared utilities and configuration for yt-dlp downloaders.
"""

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yt_dlp
from config import MAX_FILE_SIZE
from utils.logger import setup_logger
from utils.gokapi_utils import upload_to_gokapi

logger = setup_logger(__name__)

DEFAULT_YTDLP_NETWORK_OPTS: dict[str, Any] = {
    'retries': 5,
    'socket_timeout': 40,
    'http_chunk_size': 10_485_760,  # 10 MB
    'fragment_retries': 5,
    'skip_unavailable_fragments': True,
    'abort_on_unavailable_fragments': False,
    'concurrent_fragment_downloads': 4,
    'continuedl': False,
    'noplaylist': True,
    'remote_components': ['ejs:github'],
}

_NETWORK_TIMEOUT_SIGNATURES = (
    'Read timed out',
    'Connection timed out',
    'Timed out',
    'Connection reset by peer',
    'UNEXPECTED_EOF_WHILE_READING',
    'EOF occurred in violation of protocol',
    'fragment not found',
    'HTTP Error 403',
)


def classify_download_error_kind(message: str) -> str:
    """Classifies the type of DownloadError for correct logging level and flow control."""
    msg_lower = message.lower()
    if 'requested format is not available' in msg_lower:
        return 'FORMAT_UNAVAILABLE'
    if any(signature in msg_lower for signature in ('http error 403', 'forbidden', 'login required', 'private video')):
        return 'ACCESS_RESTRICTED'
    if any(
        signature in msg_lower
        for signature in (
            'requires a javascript runtime',
            'nsig extraction failed',
            'signature extraction failed',
            'unable to extract initial player response',
            'remote components',
        )
    ):
        return 'EXTRACTOR_RUNTIME'
    if any(signature.lower() in msg_lower for signature in _NETWORK_TIMEOUT_SIGNATURES):
        return 'NETWORK_TIMEOUT'
    return 'UNKNOWN'


def apply_network_opts(options: dict[str, Any]) -> None:
    """Applies default network options to yt-dlp config dict."""
    options.update(DEFAULT_YTDLP_NETWORK_OPTS)


def execute_with_backoff(description: str, func: Callable[[], Path | str], max_attempts: int = 3) -> Path | str:
    """Executes a downloader function with exponential backoff on network timeouts."""
    for attempt in range(1, max_attempts + 1):
        try:
            return func()
        except yt_dlp.utils.DownloadError as e:
            message = str(e)
            error_kind = classify_download_error_kind(message)
            if error_kind == 'NETWORK_TIMEOUT':
                if attempt == max_attempts:
                    logger.error(
                        "%s failed after %s attempts due to timeout: %s",
                        description,
                        attempt,
                        message,
                        exc_info=True,
                    )
                    raise
                delay = min(2 ** attempt, 30)
                logger.warning(
                    "%s: timeout (attempt %s/%s). Retrying in %ss",
                    description,
                    attempt,
                    max_attempts,
                    delay,
                )
                time.sleep(delay)
                continue
            if error_kind in {'FORMAT_UNAVAILABLE', 'ACCESS_RESTRICTED'}:
                logger.warning(
                    "%s: expected yt-dlp error (%s): %s",
                    description,
                    error_kind,
                    message
                )
            else:
                logger.error("%s: download error: %s", description, message, exc_info=True)
            raise


def finalize_downloaded_file(downloaded_file: Path, force_local: bool) -> Path | str:
    """Handles size limits by keeping the file locally or uploading to Gokapi."""
    file_size = downloaded_file.stat().st_size
    if force_local or file_size <= MAX_FILE_SIZE:
        return downloaded_file

    logger.warning(
        "File size of %s (%s bytes) exceeds Telegram limit. Uploading to Gokapi.",
        downloaded_file,
        file_size,
    )
    try:
        success, link_or_error = upload_to_gokapi(downloaded_file)
        if success:
            logger.info("File uploaded to Gokapi: %s", link_or_error)
            return link_or_error
        raise Exception(f"Upload server unavailable: {link_or_error}")
    finally:
        try:
            if downloaded_file.exists():
                downloaded_file.unlink()
                logger.info("Local file %s deleted after upload attempt.", downloaded_file)
        except Exception as e_del:
            logger.error("Error deleting local file %s: %s", downloaded_file, e_del)
