"""Безопасная классификация ошибок без раскрытия внутреннего состояния."""

from messages import (
    CHOOSE_ANOTHER_FORMAT,
    USER_ERROR_WITH_CODE,
    USER_NETWORK_ERROR_WITH_CODE,
    USER_PLATFORM_ERROR_WITH_CODE,
)


def youtube_error_code(error_msg: str) -> str:
    """Возвращает категорию частой ошибки YouTube/yt-dlp."""
    msg_lower = error_msg.lower()
    if "requested format is not available" in msg_lower:
        return "FORMAT_UNAVAILABLE"
    if any(
        signature in msg_lower
        for signature in (
            "http error 403",
            "forbidden",
            "sign in to confirm your age",
            "login required",
            "this video is unavailable",
            "private video",
        )
    ):
        return "ACCESS_RESTRICTED"
    if any(
        signature in msg_lower
        for signature in (
            "read timed out",
            "connection timed out",
            "timed out",
            "connection reset by peer",
            "unexpected_eof_while_reading",
            "eof occurred in violation of protocol",
            "network is unreachable",
        )
    ):
        return "NETWORK_TIMEOUT"
    if "ffmpeg" in msg_lower and any(
        signature in msg_lower for signature in ("not found", "is not installed", "ffprobe")
    ):
        return "FFMPEG_MISSING"
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
    return "UNKNOWN"


def classify_internal_error_category(platform: str, error_msg: str) -> str:
    """Классифицирует внутреннюю ошибку без возврата её текста пользователю."""
    msg_lower = error_msg.lower()
    if platform == "youtube":
        return youtube_error_code(error_msg)
    if any(
        signature in msg_lower
        for signature in ("timed out", "network", "connection reset", "ssl", "eof")
    ):
        return "NETWORK"
    if any(
        signature in msg_lower
        for signature in ("rate-limit", "too many requests", "лимит запросов")
    ):
        return "RATE_LIMIT"
    if any(
        signature in msg_lower
        for signature in (
            "login required",
            "sign in",
            "private",
            "forbidden",
            "blocked",
            "unavailable",
            "ограничил доступ",
            "ограничения",
            "блокировки",
            "авторизац",
            "недоступ",
        )
    ):
        return "ACCESS"
    if platform == "instagram" and "story" in msg_lower and "не поддерживается" in msg_lower:
        return "STORY_UNSUPPORTED"
    return "UNKNOWN"


def build_public_error_message(
    platform: str,
    error_code: str,
    error_msg: str,
) -> str:
    """Строит безопасное пользовательское сообщение с диагностическим кодом."""
    category = classify_internal_error_category(platform, error_msg)
    if platform == "youtube" and category == "FORMAT_UNAVAILABLE":
        return CHOOSE_ANOTHER_FORMAT.format(
            error="Выбранный формат сейчас недоступен."
        )
    if category in {"NETWORK", "NETWORK_TIMEOUT"}:
        return USER_NETWORK_ERROR_WITH_CODE.format(error_code=error_code)
    if category == "STORY_UNSUPPORTED":
        return (
            "📛 Скачивание Instagram Stories не поддерживается.\n\n"
            "Stories — это временный контент (24 часа), и Instagram "
            "ограничивает их загрузку через API.\n\n"
            "Попробуйте скачать обычный пост, Reel или видео из IGTV."
        )
    platform_name = {
        "youtube": "YouTube",
        "tiktok": "TikTok",
        "instagram": "Instagram",
        "rutube": "Rutube",
        "vk": "VK Video",
    }.get(platform)
    if platform_name:
        return USER_PLATFORM_ERROR_WITH_CODE.format(
            platform=platform_name,
            error_code=error_code,
        )
    return USER_ERROR_WITH_CODE.format(error_code=error_code)
