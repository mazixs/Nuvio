"""Тесты доставки фото-постов прямыми ссылками.

Решение здесь «всё или ничего»: половина картинок ссылкой, половина файлом
означала бы разный порядок отправки и разное качество в одном посте. Поэтому
если хоть одна ссылка непригодна, весь пост идёт обычным путём.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import telegram

from utils import telegram_utils, tiktok_instagram_utils
from utils.url_delivery import PhotoPostHandoff, UrlHandoff


MB = 1024 * 1024
IMAGES = [
    "https://p16-sign.tiktokcdn-us.com/obj/image-1.jpeg",
    "https://p16-sign.tiktokcdn-us.com/obj/image-2.jpeg",
]
AUDIO = "https://v16-ies-music.tiktokcdn-us.com/obj/track.mp3"
REFERER = "https://www.tiktok.com/"

pytestmark = pytest.mark.unit


@pytest.fixture
def sizes(monkeypatch):
    """Подменяет замер размера на управляемую таблицу."""
    table: dict[str, int | None] = {}
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "probe_remote_size",
        lambda url, referer=None: table.get(url),
    )
    return table


def test_whole_post_goes_by_link(sizes):
    sizes.update({IMAGES[0]: 300 * 1024, IMAGES[1]: 400 * 1024, AUDIO: 250 * 1024})

    plan = tiktok_instagram_utils.resolve_photo_post_handoff(IMAGES, AUDIO, REFERER)

    assert plan is not None
    assert [item.url for item in plan.images] == IMAGES
    assert all(item.kind == "photo" for item in plan.images)
    assert plan.audio is not None
    assert plan.audio.kind == "audio"


def test_post_without_sound_still_goes_by_link(sizes):
    sizes.update({IMAGES[0]: 300 * 1024, IMAGES[1]: 400 * 1024})

    plan = tiktok_instagram_utils.resolve_photo_post_handoff(IMAGES, None, REFERER)

    assert plan is not None
    assert plan.audio is None


def test_one_unmeasurable_image_cancels_the_whole_post(sizes):
    sizes.update({IMAGES[0]: 300 * 1024, AUDIO: 250 * 1024})

    assert tiktok_instagram_utils.resolve_photo_post_handoff(IMAGES, AUDIO, REFERER) is None


def test_oversized_image_cancels_the_whole_post(sizes):
    """Лимит на фото по ссылке — 5 МБ, и он ниже лимита на видео."""
    sizes.update({IMAGES[0]: 300 * 1024, IMAGES[1]: 6 * MB, AUDIO: 250 * 1024})

    assert tiktok_instagram_utils.resolve_photo_post_handoff(IMAGES, AUDIO, REFERER) is None


def test_unusable_sound_cancels_the_whole_post(sizes):
    """Картинки без звука — это уже другой пост, поэтому лучше обычный путь."""
    sizes.update({IMAGES[0]: 300 * 1024, IMAGES[1]: 400 * 1024})

    assert tiktok_instagram_utils.resolve_photo_post_handoff(IMAGES, AUDIO, REFERER) is None


def test_image_outside_the_allowlist_cancels_the_whole_post(sizes):
    internal = "http://telegram-bot-api:8081/file/image.jpg"
    sizes.update({IMAGES[0]: 300 * 1024, internal: 100 * 1024})

    plan = tiktok_instagram_utils.resolve_photo_post_handoff(
        [IMAGES[0], internal], None, REFERER
    )

    assert plan is None


def test_empty_post_has_nothing_to_hand_over(sizes):
    assert tiktok_instagram_utils.resolve_photo_post_handoff([], None, REFERER) is None


# --- отправка поста ссылками ------------------------------------------------


def _query(reply_photo, reply_audio=None):
    return SimpleNamespace(
        message=SimpleNamespace(reply_photo=reply_photo, reply_audio=reply_audio)
    )


def _plan(audio: bool = True):
    images = tuple(
        UrlHandoff(url=url, kind="photo", size=300 * 1024) for url in IMAGES
    )
    return PhotoPostHandoff(
        images=images,
        audio=UrlHandoff(url=AUDIO, kind="audio", size=250 * 1024) if audio else None,
    )


def test_post_is_sent_as_links_in_order():
    reply_photo = AsyncMock()
    reply_audio = AsyncMock()

    sent = asyncio.run(
        telegram_utils._deliver_photo_post_by_url(
            _query(reply_photo, reply_audio), _plan()
        )
    )

    assert sent is True
    assert [call.kwargs["photo"] for call in reply_photo.await_args_list] == IMAGES
    assert reply_audio.await_args.kwargs["audio"] == AUDIO


def test_refusal_on_the_first_image_falls_back_quietly():
    """Ничего ещё не отправлено — можно спокойно уйти на обычный путь."""
    reply_photo = AsyncMock(side_effect=telegram.error.BadRequest("отказ"))

    sent = asyncio.run(
        telegram_utils._deliver_photo_post_by_url(_query(reply_photo), _plan(audio=False))
    )

    assert sent is False


def test_refusal_in_the_middle_is_raised_instead_of_resending():
    """Первая картинка уже у пользователя: повтор поста дал бы дубли."""
    reply_photo = AsyncMock(
        side_effect=[None, telegram.error.BadRequest("отказ на второй")]
    )

    with pytest.raises(telegram.error.BadRequest):
        asyncio.run(
            telegram_utils._deliver_photo_post_by_url(
                _query(reply_photo), _plan(audio=False)
            )
        )
