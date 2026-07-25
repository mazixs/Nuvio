"""Выбор формата YouTube для кнопки «отправить видео в Telegram».

Кнопка обещает файл, который просто откроется в Telegram, поэтому здесь два
критерия, и оба важнее максимального разрешения:

1. **Бюджет.** Он приходит извне и равен реальному лимиту доставки: 2000 МБ при
   локальном Bot API, 50 МБ при облачном. Раньше границы были захардкожены под
   облачный режим (35 МБ на видео + 15 МБ на звук), и локальный сервер это
   никак не учитывал.
2. **Потолок разрешения.** Кнопка обещает «просто отправь», а не «максимум
   возможного»: 4K на 32-минутном ролике — это 467–926 МБ и минуты отправки,
   причём H.264 в таком разрешении YouTube не отдаёт вовсе. Кто хочет больше,
   берёт формат в меню осознанно.
3. **Пригодность кодека — как тай-брейк, а не как приоритет.** H.264 и AAC
   проигрываются везде (ADR-001 фиксировал, чем заканчивается нестандартный
   кодек на iOS), поэтому при равном разрешении выбирается именно они. Но
   ставить кодек выше разрешения нельзя: в облачном режиме под 50 МБ H.264
   влезает только в 240p, тогда как AV1 даёт 480p — это была бы потеря
   качества там, где её никто не просил.

Модуль чистый: без сети, без файлов и без обращения к config.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


# Префиксы кодеков, которые Telegram проигрывает без оговорок на всех клиентах.
TELEGRAM_READY_VIDEO_CODECS = ("avc1", "h264")
TELEGRAM_READY_AUDIO_CODECS = ("mp4a", "aac")

# Потолок разрешения для кнопки. Выше — только через меню форматов.
TG_VIDEO_MAX_HEIGHT = 1080


@dataclass(frozen=True)
class TgVideoChoice:
    """Выбранный формат и то, чем за него платим."""

    format_id: str
    height: int | None
    ext: str
    kind: str
    total_size: int


def _codec_rank(codec: Any, ready_prefixes: tuple[str, ...]) -> int:
    """0 — кодек проигрывается везде, 1 — остальные."""
    normalized = str(codec or "").lower()
    return 0 if normalized.startswith(ready_prefixes) else 1


def _sized(formats: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Оставляет форматы с известным размером.

    Формат без размера сверить с бюджетом нельзя, а угадывать здесь нечего:
    решение о таких форматах принимает вызывающий код.
    """
    return [fmt for fmt in formats if isinstance(fmt.get("filesize"), int)]


def _video_sort_key(fmt: dict[str, Any]) -> tuple[int, int, int]:
    """Разрешение важнее кодека, кодек важнее размера."""
    return (
        -(fmt.get("height") or 0),
        _codec_rank(fmt.get("vcodec"), TELEGRAM_READY_VIDEO_CODECS),
        fmt["filesize"],
    )


def _audio_sort_key(fmt: dict[str, Any]) -> tuple[int, int]:
    return (
        _codec_rank(fmt.get("acodec"), TELEGRAM_READY_AUDIO_CODECS),
        -fmt["filesize"],
    )


@dataclass(frozen=True)
class VideoOption:
    """Один пункт меню разрешений."""

    height: int
    format_id: str
    ext: str
    size: int  # 0 — размер неизвестен


@dataclass(frozen=True)
class AudioOption:
    """Один пункт меню звуковых дорожек."""

    format_id: str
    ext: str
    size: int  # 0 — размер неизвестен


def list_audio_options(
    audio_only: Sequence[dict[str, Any]], budget_bytes: int
) -> list[AudioOption]:
    """Возвращает родные дорожки, пригодные для отправки как аудио.

    Opus в WebM Telegram аудиофайлом не считает, поэтому такие дорожки в меню
    не попадают: предлагать их — значит обещать то, что не воспроизведётся.
    Перекодирование здесь не рассматривается, речь только о родном звуке.
    """
    playable = [
        fmt
        for fmt in audio_only
        if _codec_rank(fmt.get("acodec"), TELEGRAM_READY_AUDIO_CODECS) == 0
        and (
            not isinstance(fmt.get("filesize"), int)
            or fmt["filesize"] <= budget_bytes
        )
    ]
    return [
        AudioOption(
            format_id=str(fmt["format_id"]),
            ext=fmt.get("ext") or "m4a",
            size=fmt["filesize"] if isinstance(fmt.get("filesize"), int) else 0,
        )
        for fmt in sorted(
            playable,
            key=lambda fmt: -(
                fmt["filesize"] if isinstance(fmt.get("filesize"), int) else 0
            ),
        )
    ]


def _best_pair(
    video: dict[str, Any], audios: Sequence[dict[str, Any]], budget_bytes: int
) -> tuple[str, int] | None:
    """Подбирает к видеодорожке звук, с которым пара влезает в бюджет."""
    if not isinstance(video.get("filesize"), int):
        # Размер неизвестен — сверять с бюджетом нечего, звук берём лучший.
        if not audios:
            return None
        return f"{video['format_id']}+{audios[0]['format_id']}", 0

    for audio in audios:
        total = video["filesize"] + audio["filesize"]
        if total <= budget_bytes:
            return f"{video['format_id']}+{audio['format_id']}", total
    return None


def list_video_options(
    video_only: Sequence[dict[str, Any]],
    audio_only: Sequence[dict[str, Any]],
    combined: Sequence[dict[str, Any]],
    budget_bytes: int,
) -> list[VideoOption]:
    """Возвращает по одному пункту на разрешение, от высокого к низкому.

    Меню обещает выбор, поэтому потолок разрешения здесь не действует — в
    отличие от кнопки «отправить в Telegram». Отсекается только то, что не
    влезает в лимит доставки: предлагать заведомо неотправляемый файл значит
    обещать невыполнимое.

    Внутри одного разрешения работают те же правила, что у кнопки: сначала
    H.264, затем меньший размер; готовый ``combined`` предпочтительнее пары,
    потому что его не нужно склеивать FFmpeg.
    """
    audios = sorted(_sized(audio_only), key=_audio_sort_key)
    options: dict[int, VideoOption] = {}

    ready = sorted(
        (fmt for fmt in _sized(combined) if fmt["filesize"] <= budget_bytes),
        key=_video_sort_key,
    )
    for fmt in ready:
        height = fmt.get("height") or 0
        options.setdefault(
            height,
            VideoOption(
                height=height,
                format_id=str(fmt["format_id"]),
                ext=fmt.get("ext") or "mp4",
                size=fmt["filesize"],
            ),
        )

    # Форматы без размера сортируются после форматов с размером: у них тот же
    # ключ, но проверить бюджет нельзя, поэтому в приоритете известное.
    tracks = sorted(_sized(video_only), key=_video_sort_key) + [
        fmt for fmt in video_only if not isinstance(fmt.get("filesize"), int)
    ]
    for fmt in tracks:
        height = fmt.get("height") or 0
        if height in options:
            continue
        pair = _best_pair(fmt, audios, budget_bytes)
        if pair:
            format_id, size = pair
            options[height] = VideoOption(
                height=height,
                format_id=format_id,
                ext=fmt.get("ext") or "mp4",
                size=size,
            )

    return sorted(options.values(), key=lambda option: -option.height)


def select_tg_video_format(
    video_only: Sequence[dict[str, Any]],
    audio_only: Sequence[dict[str, Any]],
    combined: Sequence[dict[str, Any]],
    budget_bytes: int,
    max_height: int = TG_VIDEO_MAX_HEIGHT,
) -> TgVideoChoice | None:
    """Возвращает формат, укладывающийся в ``budget_bytes``.

    Пара «видео + звук» проверяется раньше готового ``combined``: склейка стоит
    секунды FFmpeg, а разница в качестве обычно кратная — у YouTube единственный
    combined-формат чаще всего 360p.

    Returns:
        None, если ни пара, ни combined с известным размером в бюджет не влезли.
    """
    videos = sorted(
        (fmt for fmt in _sized(video_only) if (fmt.get("height") or 0) <= max_height),
        key=_video_sort_key,
    )
    audios = sorted(_sized(audio_only), key=_audio_sort_key)

    for video in videos:
        for audio in audios:
            total = video["filesize"] + audio["filesize"]
            if total <= budget_bytes:
                return TgVideoChoice(
                    format_id=f"{video['format_id']}+{audio['format_id']}",
                    height=video.get("height"),
                    ext=video.get("ext") or "mp4",
                    kind="combined_manual",
                    total_size=total,
                )

    for fmt in sorted(
        (f for f in _sized(combined) if (f.get("height") or 0) <= max_height),
        key=_video_sort_key,
    ):
        if fmt["filesize"] <= budget_bytes:
            return TgVideoChoice(
                format_id=str(fmt["format_id"]),
                height=fmt.get("height"),
                ext=fmt.get("ext") or "mp4",
                kind="combined",
                total_size=fmt["filesize"],
            )

    return None
