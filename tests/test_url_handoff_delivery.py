"""Тесты доставки медиа прямой ссылкой вместо файла.

Telegram скачивает ссылку сам, поэтому от бота требуется только передать её и
корректно отступить, если Telegram ссылку не принял.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import telegram

from utils import telegram_utils
from utils.url_delivery import UrlHandoff


MB = 1024 * 1024
PAGE_URL = "https://www.tiktok.com/@user/video/7643812958950362389"
MEDIA_URL = "https://v16m.tiktokcdn-us.com/abc/video.mp4"

pytestmark = pytest.mark.unit


def _query(**senders):
    return SimpleNamespace(message=SimpleNamespace(**senders))


def _sent(kind: str, file_id: str = "AGADnew"):
    """Ответ Telegram на успешную отправку."""
    media = SimpleNamespace(
        file_id=file_id, file_unique_id="unique", file_size=2 * MB, duration=11
    )
    fields = {"video": None, "audio": None, "document": None, "photo": None}
    fields[kind] = media
    return SimpleNamespace(**fields)


def _deliver(query, plan, **kwargs):
    return asyncio.run(
        telegram_utils._deliver_by_url(
            query, plan, PAGE_URL, "tiktok", **kwargs
        )
    )


def test_video_is_handed_over_as_a_link():
    reply_video = AsyncMock(return_value=_sent("video"))
    plan = UrlHandoff(url=MEDIA_URL, kind="video", size=2 * MB)

    delivered = _deliver(_query(reply_video=reply_video), plan)

    assert delivered is True
    assert reply_video.await_args.kwargs["video"] == MEDIA_URL
    assert reply_video.await_args.kwargs["supports_streaming"] is True


def test_audio_is_handed_over_as_a_link():
    reply_audio = AsyncMock(return_value=_sent("audio"))
    plan = UrlHandoff(url=MEDIA_URL, kind="audio", size=MB)

    delivered = _deliver(_query(reply_audio=reply_audio), plan)

    assert delivered is True
    assert reply_audio.await_args.kwargs["audio"] == MEDIA_URL


def test_photo_is_handed_over_as_a_link():
    reply_photo = AsyncMock(return_value=_sent("photo"))
    plan = UrlHandoff(url=MEDIA_URL, kind="photo", size=MB)

    delivered = _deliver(_query(reply_photo=reply_photo), plan)

    assert delivered is True
    assert reply_photo.await_args.kwargs["photo"] == MEDIA_URL


def test_refusal_reports_not_delivered_so_caller_falls_back():
    """Отказ Telegram — не ошибка, а сигнал качать файл самим."""
    reply_video = AsyncMock(
        side_effect=telegram.error.BadRequest("failed to get HTTP URL content")
    )
    plan = UrlHandoff(url=MEDIA_URL, kind="video", size=2 * MB)

    delivered = _deliver(_query(reply_video=reply_video), plan)

    assert delivered is False


def test_timeout_reports_not_delivered():
    """Ссылку, которую Telegram не смог забрать за отведённое время, тоже роняем в откат."""
    reply_video = AsyncMock(side_effect=telegram.error.TimedOut())
    plan = UrlHandoff(url=MEDIA_URL, kind="video", size=2 * MB)

    delivered = _deliver(_query(reply_video=reply_video), plan)

    assert delivered is False


def test_file_id_from_a_link_is_cached(monkeypatch):
    """Ссылка не отменяет кэш: Telegram возвращает file_id и его надо сохранить."""
    saved = []
    monkeypatch.setattr(telegram_utils.telegram_cache, "set", saved.append)
    reply_video = AsyncMock(return_value=_sent("video", file_id="AGADfromlink"))
    plan = UrlHandoff(url=MEDIA_URL, kind="video", size=2 * MB)

    _deliver(
        _query(reply_video=reply_video),
        plan,
        cache_format_id="tiktok_video",
        video_info={"title": "ролик"},
    )

    assert len(saved) == 1
    assert saved[0].file_id == "AGADfromlink"
    assert saved[0].url == PAGE_URL
    assert saved[0].format_id == "tiktok_video"
    assert saved[0].title == "ролик"


def test_nothing_is_cached_without_a_cache_key(monkeypatch):
    saved = []
    monkeypatch.setattr(telegram_utils.telegram_cache, "set", saved.append)
    reply_video = AsyncMock(return_value=_sent("video"))
    plan = UrlHandoff(url=MEDIA_URL, kind="video", size=2 * MB)

    _deliver(_query(reply_video=reply_video), plan)

    assert saved == []


def test_photo_is_not_cached(monkeypatch):
    """Кэш file_id рассчитан на видео, аудио и документы — фото туда не пишем."""
    saved = []
    monkeypatch.setattr(telegram_utils.telegram_cache, "set", saved.append)
    reply_photo = AsyncMock(return_value=_sent("photo"))
    plan = UrlHandoff(url=MEDIA_URL, kind="photo", size=MB)

    _deliver(
        _query(reply_photo=reply_photo), plan, cache_format_id="instagram_photo"
    )

    assert saved == []
