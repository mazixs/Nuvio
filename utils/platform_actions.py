"""Чистые решения, связывающие пользовательские действия с платформой."""


DIRECT_VIDEO_CACHE_KEY = "direct_video"


def cache_key_for_main_action(platform: str, action: str) -> str | None:
    """Возвращает ключ кэша для основной кнопки платформы."""
    if platform in {"tiktok", "instagram"} and action.endswith("_download"):
        return DIRECT_VIDEO_CACHE_KEY
    if platform == "youtube" and action == "tg_video":
        return "tg_video"
    return None


def cache_key_for_format_selection(
    content_type: str,
    format_id: str,
) -> str | None:
    """Возвращает ключ кэша для расширенного формата YouTube."""
    if content_type == "combined":
        return f"combined:{format_id}"
    if content_type == "video_only":
        return f"video_only:{format_id}"
    if content_type == "best":
        return "best"
    return None
