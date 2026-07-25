"""Чистые решения по субтитрам: какие языки предложить и как их отдать.

Раньше язык и формат были зашиты: всегда SRT и всегда «русские, иначе
английские, иначе первые попавшиеся». Теперь пользователь выбирает сам, поэтому
здесь живёт разбор доступных дорожек, проверка выбора, пришедшего из
callback_data, и превращение SRT в обычный текст.

Модуль чистый: ни сети, ни файлов.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


__all__ = [
    "SUBTITLE_FORMATS",
    "SUBTITLE_LANGUAGES",
    "SubtitleLanguage",
    "available_subtitle_languages",
    "parse_subtitle_choice",
    "srt_to_text",
]

# Форматы, которые предлагаются пользователю. SRT и VTT yt-dlp отдаёт сам, TXT
# получается из SRT — читать субтитры без таймкодов иногда нужнее самих
# субтитров.
SUBTITLE_FORMATS = ("srt", "vtt", "txt")

# Языки и их подписи. Список намеренно короткий: у популярных роликов десятки
# автопереводов, и вываливать их все — не выбор, а стена кнопок.
SUBTITLE_LANGUAGES = (("ru", "🇷🇺 Русский"), ("en", "🇬🇧 English"))

_TIMECODE = re.compile(r"^\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}\s*-->")
_INDEX = re.compile(r"^\d+$")
_TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class SubtitleLanguage:
    """Доступная языковая дорожка субтитров."""

    code: str
    label: str
    is_auto: bool


def _has_language(tracks: dict[str, Any], code: str) -> bool:
    """Есть ли дорожка на этом языке, включая региональные варианты.

    YouTube отдаёт автоперевод под кодами вида ``en-US`` и ``ru-orig``, поэтому
    сравнивать точным равенством нельзя.
    """
    return any(
        key == code or key.lower().startswith(f"{code}-") for key in tracks or {}
    )


def available_subtitle_languages(video_info: dict[str, Any]) -> list[SubtitleLanguage]:
    """Возвращает языки, которые есть смысл предлагать."""
    manual = (video_info or {}).get("subtitles") or {}
    automatic = (video_info or {}).get("automatic_captions") or {}

    languages: list[SubtitleLanguage] = []
    for code, label in SUBTITLE_LANGUAGES:
        if _has_language(manual, code):
            languages.append(SubtitleLanguage(code=code, label=label, is_auto=False))
        elif _has_language(automatic, code):
            languages.append(
                SubtitleLanguage(code=code, label=f"{label} (авто)", is_auto=True)
            )
    return languages


def parse_subtitle_choice(value: str) -> tuple[str, str] | None:
    """Разбирает значение вида ``ru:srt`` из callback_data.

    Returns:
        Пара «язык, формат» либо ``None``, если значение не из наших списков.
    """
    parts = (value or "").split(":")
    if len(parts) != 2:
        return None

    language, subtitle_format = parts
    known_languages = {code for code, _ in SUBTITLE_LANGUAGES}
    if language not in known_languages or subtitle_format not in SUBTITLE_FORMATS:
        return None
    return language, subtitle_format


def srt_to_text(content: str) -> str:
    """Убирает из SRT нумерацию, таймкоды и разметку, оставляя реплики.

    Подряд идущие одинаковые строки схлопываются: автосубтитры повторяют фразу
    в каждом кадре, и без этого текст читать невозможно.
    """
    lines: list[str] = []
    for raw in (content or "").splitlines():
        line = _TAG.sub("", raw).strip()
        if not line or _INDEX.match(line) or _TIMECODE.match(line):
            continue
        if lines and lines[-1] == line:
            continue
        lines.append(line)
    return "\n".join(lines)
