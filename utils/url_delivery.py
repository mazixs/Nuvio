"""Решение о том, отдавать ли медиа Telegram прямой ссылкой вместо файла.

Telegram скачивает такую ссылку сам, поэтому ни диск, ни исходящий трафик бота
не задействуются вовсе: на измеренном запросе TikTok это заменяет 4.5 секунды
(скачивание плюс выгрузка) на 0.6–1.7 секунды.

Границы применимости измерены на живом сервере, разбор — в
docs/technical/latency-disk-network-research.md §8. Два вывода оттуда важны для
этого модуля:

* скачивает ссылку инфраструктура Telegram, а не локальный Bot API, — поэтому
  режим ``--local`` лимит 20 МБ не снимает и внутренние адреса недостижимы
  принципиально;
* переданный размер обязателен: без него нельзя обещать соблюдение лимита, а
  отказ Telegram стоит до 15 секунд ожидания.

Здесь только чистое решение — ни сети, ни файловых операций.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from utils.fast_path import is_allowed_media_url
from utils.instagram_fast_path import ALLOWED_INSTAGRAM_MEDIA_DOMAINS
from utils.tiktok_fast_path import ALLOWED_MEDIA_DOMAINS as ALLOWED_TIKTOK_MEDIA_DOMAINS


__all__ = [
    "ALLOWED_HANDOFF_DOMAINS",
    "MAX_HANDOFF_BYTES",
    "MAX_PHOTO_HANDOFF_BYTES",
    "HandoffKind",
    "PhotoPostHandoff",
    "UrlHandoff",
    "find_format_url",
    "handoff_limit_for",
    "plan_url_handoff",
]

HandoffKind = Literal["video", "audio", "photo"]

# Лимит Bot API на доставку по ссылке. Проверено отправкой файлов точного
# размера: 19.5 МБ принято за 0.1 с, 30.6 и 37.2 МБ отвергнуты с
# `failed to get HTTP URL content`.
MAX_HANDOFF_BYTES = 20 * 1024 * 1024

# Для фотографий лимит ниже. Значение документированное: наши картинки
# (0.13 МБ у Instagram) до него не достают, поэтому эмпирически граница не
# нащупана — расхождение прикрыто откатом на обычный путь.
MAX_PHOTO_HANDOFF_BYTES = 5 * 1024 * 1024

# Домены, ссылки с которых разрешено передавать Telegram. Проверено на живых
# ссылках: TikTok, Instagram и progressive-форматы YouTube принимаются, VK
# отдаёт 400 любому клиенту кроме исходного, Rutube прямых ссылок не даёт
# вообще. Allowlist существует не ради Telegram, а ради нас: без него
# подменённый ответ резолвера мог бы увести отправку на внутренний адрес.
#
# Набор собирается из allowlist'ов быстрых путей, а не переписывается заново:
# домен, откуда мы качаем сами, пригоден и для передачи ссылки, а две
# независимые копии рано или поздно разошлись бы.
ALLOWED_HANDOFF_DOMAINS = (
    ALLOWED_TIKTOK_MEDIA_DOMAINS
    | ALLOWED_INSTAGRAM_MEDIA_DOMAINS
    | frozenset({"googlevideo.com"})
)


@dataclass(frozen=True)
class UrlHandoff:
    """Подтверждённое решение отдать ссылку вместо файла."""

    url: str
    kind: HandoffKind
    size: int


@dataclass(frozen=True)
class PhotoPostHandoff:
    """Решение отдать ссылками весь фото-пост целиком."""

    images: tuple[UrlHandoff, ...]
    audio: UrlHandoff | None


def handoff_limit_for(kind: HandoffKind) -> int:
    """Возвращает лимит доставки по ссылке для конкретного вида медиа."""
    return MAX_PHOTO_HANDOFF_BYTES if kind == "photo" else MAX_HANDOFF_BYTES


def plan_url_handoff(
    url: str | None, kind: HandoffKind, size: int | None
) -> UrlHandoff | None:
    """Решает, можно ли отдать это медиа ссылкой.

    Returns:
        Решение с проверенной ссылкой либо ``None``, если доставка ссылкой
        неприменима — тогда вызывающий код обязан идти обычным путём.
    """
    if not size or size <= 0 or size > handoff_limit_for(kind):
        return None
    if not url or not is_allowed_media_url(url, ALLOWED_HANDOFF_DOMAINS):
        return None
    return UrlHandoff(url=url, kind=kind, size=size)


def find_format_url(video_info: dict | None, format_id: str) -> str | None:
    """Находит прямую ссылку на формат в ответе yt-dlp.

    Составные идентификаторы вида ``299+140`` отбрасываются сразу: такой формат
    собирается FFmpeg из двух файлов, и одной ссылки на результат не существует.
    """
    if not video_info or "+" in format_id:
        return None
    for candidate in video_info.get("formats") or []:
        if str(candidate.get("format_id")) == format_id:
            return candidate.get("url") or None
    return None
