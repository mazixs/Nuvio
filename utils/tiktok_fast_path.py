"""Чистые решения быстрого пути TikTok.

Резолвер отдаёт прямую ссылку на H.264-видео со звуком, поэтому быстрый путь
обходится без yt-dlp и без перекодирования. Здесь только разбор ответа и
проверки пригодности — без сети и без файловых операций.

Обоснование и замеры: docs/technical/latency-disk-network-research.md
"""

from __future__ import annotations

from dataclasses import dataclass


# Предел Bot API на скачивание файла по URL силами Telegram (не-фото).
URL_HANDOFF_LIMIT_BYTES = 20_000_000

# Допустимое расхождение длительности звука и видео, секунды.
AUDIO_DURATION_TOLERANCE_SECONDS = 2


class FastPathUnavailable(Exception):
    """Быстрый путь неприменим — вызывающий код обязан использовать yt-dlp."""


@dataclass(frozen=True)
class FastMedia:
    """Прямые ссылки и метаданные, достаточные для отправки без обработки."""

    video_url: str
    size: int
    duration: int
    title: str
    cover: str | None
    audio_url: str | None
    audio_is_video_sound: bool


def audio_matches_video(music_info: dict, video_duration: int) -> bool:
    """Проверяет, что ``music`` — это звук самого видео, а не библиотечный трек.

    Резолвер возвращает в ``music`` звуковую дорожку публикации. Для видео с
    лицензированным треком это полная песня: проверено на реальном 35-секундном
    видео, где ``music`` пришёл на 168 секунд. Поэтому требуем и флаг
    ``original``, и совпадение длительности.
    """
    if not music_info:
        return False
    if music_info.get("original") is not True:
        return False

    music_duration = music_info.get("duration")
    if music_duration is None:
        return False

    try:
        drift = abs(int(music_duration) - int(video_duration))
    except (TypeError, ValueError):
        return False

    return drift <= AUDIO_DURATION_TOLERANCE_SECONDS


def fits_url_handoff(size: int) -> bool:
    """Уложится ли файл в лимит Telegram на скачивание по URL."""
    return 0 < size <= URL_HANDOFF_LIMIT_BYTES


def parse_fast_media(payload: dict) -> FastMedia:
    """Разбирает ответ резолвера в набор прямых ссылок.

    Raises:
        FastPathUnavailable: ответ с ошибкой, без прямой ссылки или фото-пост.
    """
    if payload.get("code") != 0:
        raise FastPathUnavailable(
            f"резолвер отказал: {payload.get('msg') or 'неизвестная ошибка'}"
        )

    data = payload.get("data") or {}

    if data.get("images"):
        raise FastPathUnavailable(
            "это TikTok фото-пост, быстрый путь видео неприменим"
        )

    video_url = data.get("play")
    if not video_url:
        raise FastPathUnavailable("резолвер не вернул прямую ссылку на видео")

    duration = int(data.get("duration") or 0)
    music_info = data.get("music_info") or {}
    audio_is_video_sound = audio_matches_video(music_info, duration)
    audio_url = None
    if audio_is_video_sound:
        audio_url = data.get("music") or music_info.get("play")

    return FastMedia(
        video_url=str(video_url),
        size=int(data.get("size") or 0),
        duration=duration,
        title=str(data.get("title") or ""),
        cover=data.get("cover") or data.get("origin_cover"),
        audio_url=audio_url,
        audio_is_video_sound=audio_is_video_sound,
    )
