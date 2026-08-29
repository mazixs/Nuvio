"""Безопасная классификация ошибок без раскрытия внутреннего состояния."""

from messages import (
    CHOOSE_ANOTHER_FORMAT,
    USER_ERROR_WITH_CODE,
    USER_NETWORK_ERROR_WITH_CODE,
    USER_PLATFORM_ERROR_WITH_CODE,
)


# Маркеры, по которым 403 на медиафайле отличается от запрета доступа к видео.
# CDN отказал по уже выданной ссылке, то есть видео не закрыто, а не отдаётся
# конкретный поток: так выглядит и протухшая ссылка, и смена правил выдачи на
# стороне платформы. Первое лечится повторным разбором, второе — обновлением
# yt-dlp, и различает их только серия таких отказов, поэтому категория обязана
# доходить до админов (см. `_should_notify_admins_platform_failure`).
_MEDIA_FORBIDDEN_MARKERS = (
    "unable to download video data",
    "fragment",
    "giving up after",
)


def is_media_forbidden_error(error_msg: str) -> bool:
    """Отличает 403 на самом медиафайле от запрета доступа к видео."""
    msg_lower = error_msg.lower()
    if "http error 403" not in msg_lower:
        return False
    return any(marker in msg_lower for marker in _MEDIA_FORBIDDEN_MARKERS)


def youtube_error_code(error_msg: str) -> str:
    """Возвращает категорию частой ошибки YouTube/yt-dlp."""
    msg_lower = error_msg.lower()
    if "requested format is not available" in msg_lower:
        return "FORMAT_UNAVAILABLE"
    if is_media_forbidden_error(error_msg):
        return "MEDIA_FORBIDDEN"
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
            # Формулировки yt-dlp для недоступного поста Instagram. Настоящий
            # ответ платформы (`{"message":"Media not found or unavailable"}`)
            # yt-dlp проглатывает, наружу отдавая только голый `HTTP Error 400`,
            # в котором ни одного признака выше нет. Из-за этого удалённый или
            # закрытый рил уходил в UNKNOWN, то есть в краш-репорт с трейсбеком
            # и побудкой админов: замерено пять `IG-UNKNOWN` при нуле
            # `IG-ACCESS` (ADR-002).
            "empty media response",
            "not granting access",
            "video info extraction failed",
            "media not found",
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
    if category in {"NETWORK", "NETWORK_TIMEOUT", "MEDIA_FORBIDDEN"}:
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
