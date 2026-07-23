"""Низкоуровневые решения для доставки локальных файлов в Telegram."""

from typing import Literal


MediaKind = Literal["video", "audio", "document"]

_VIDEO_SUFFIXES = {".mp4", ".webm", ".mkv", ".avi", ".mov"}
_AUDIO_SUFFIXES = {".mp3", ".m4a", ".wav", ".ogg"}


def media_kind_for_suffix(suffix: str) -> MediaKind:
    """Определяет Telegram-метод отправки по расширению файла."""
    normalized = suffix.lower()
    if normalized in _VIDEO_SUFFIXES:
        return "video"
    if normalized in _AUDIO_SUFFIXES:
        return "audio"
    return "document"
