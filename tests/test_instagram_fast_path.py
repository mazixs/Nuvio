"""Тесты чистого разбора быстрого пути Instagram."""

import pytest

from utils.fast_path import FastPathUnavailable
from utils.instagram_fast_path import (
    ALLOWED_INSTAGRAM_MEDIA_DOMAINS,
    is_allowed_instagram_media_url,
    parse_instagram_fast_media,
)


CDN_URL = "https://scontent-ams2-1.cdninstagram.com/o1/v/t2/f2/m86/video.mp4?efg=abc"


def _video_media(**overrides):
    """Ответ GraphQL для рилса, снятый с реального ответа Instagram."""
    media = {
        "code": "DbMSKU5Ba9g",
        "media_type": 2,
        "caption": {"text": "Spider-Snoop. You watching this film?"},
        "video_versions": [
            {"width": 720, "height": 1280, "type": 101, "url": CDN_URL},
            {"width": 720, "height": 1280, "type": 102, "url": CDN_URL + "&t=102"},
        ],
        "video_dash_manifest": "<MPD/>",
    }
    media.update(overrides)
    return media


@pytest.mark.unit
def test_picks_first_video_version():
    """Все версии одного рилса — один и тот же файл.

    Проверено на реальном ответе: три записи `video_versions` (type 101/102/103)
    приходят с одинаковыми 720x1280, размером 4.61 МБ и длительностью 25.79 с.
    Поэтому берём первую и не тратим запросы на сравнение.
    """
    media = parse_instagram_fast_media(_video_media())

    assert media.video_url == CDN_URL


@pytest.mark.unit
def test_falls_back_to_video_url_field():
    """Старая форма ответа отдаёт одну ссылку в `video_url`."""
    media = parse_instagram_fast_media(
        _video_media(video_versions=[], video_url=CDN_URL)
    )

    assert media.video_url == CDN_URL


@pytest.mark.unit
def test_title_comes_from_caption():
    media = parse_instagram_fast_media(_video_media())

    assert media.title == "Spider-Snoop. You watching this film?"


@pytest.mark.unit
def test_title_survives_missing_caption():
    """Без подписи имя файла всё равно должно строиться."""
    media = parse_instagram_fast_media(_video_media(caption=None))

    assert media.title == ""


@pytest.mark.unit
def test_rejects_post_without_video():
    """Фото-пост обрабатывается отдельной ветвью, быстрый путь видео не его."""
    with pytest.raises(FastPathUnavailable, match="прямой ссылки"):
        parse_instagram_fast_media(
            {"media_type": 1, "image_versions2": {"candidates": [{"url": CDN_URL}]}}
        )


@pytest.mark.unit
def test_rejects_carousel():
    """Карусель может смешивать фото и видео — её собирает отдельный сборщик.

    Отдать первое видео карусели значило бы молча потерять остальные элементы.
    """
    with pytest.raises(FastPathUnavailable, match="карусель"):
        parse_instagram_fast_media(
            _video_media(carousel_media=[{"media_type": 2}, {"media_type": 1}])
        )


@pytest.mark.unit
def test_rejects_url_outside_allowlist():
    """Ссылка из ответа — данные третьей стороны, а не наш контракт.

    Без allowlist подменённый адрес вида `http://telegram-bot-api:8081/...`
    заставил бы бота сходить во внутреннюю сеть Docker и отдать тело ответа
    запросившему пользователю.
    """
    with pytest.raises(FastPathUnavailable, match="allowlist"):
        parse_instagram_fast_media(
            _video_media(
                video_versions=[{"url": "http://telegram-bot-api:8081/secret"}]
            )
        )


@pytest.mark.unit
def test_rejects_plain_http_on_allowed_domain():
    assert not is_allowed_instagram_media_url(
        "http://scontent.cdninstagram.com/video.mp4"
    )


@pytest.mark.unit
def test_allowlist_matches_domain_suffix_not_substring():
    """Иначе хост `cdninstagram.com.evil.test` прошёл бы проверку."""
    assert is_allowed_instagram_media_url(CDN_URL)
    assert not is_allowed_instagram_media_url(
        "https://cdninstagram.com.evil.test/video.mp4"
    )


@pytest.mark.unit
def test_allowlist_covers_both_meta_cdns():
    """Instagram отдаёт медиа и с cdninstagram.com, и с fbcdn.net."""
    assert "cdninstagram.com" in ALLOWED_INSTAGRAM_MEDIA_DOMAINS
    assert "fbcdn.net" in ALLOWED_INSTAGRAM_MEDIA_DOMAINS
    assert is_allowed_instagram_media_url(
        "https://instagram.fmow1-1.fna.fbcdn.net/v/t66/video.mp4"
    )
