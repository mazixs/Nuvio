"""Тесты подготовки доставки по ссылке для TikTok и Instagram.

Резолверы обязаны знать размер: без него нельзя обещать, что Telegram уложится
в лимит 20 МБ, а его отказ обходится до 15 секунд ожидания. Размер берётся из
ответа резолвера, а если тот его не дал — одним Range-запросом.
"""

from types import SimpleNamespace

import pytest

from utils import tiktok_instagram_utils
from utils.fast_path import FastPathUnavailable
from utils.instagram_fast_path import InstagramFastMedia
from utils.tiktok_fast_path import FastMedia


MB = 1024 * 1024
TIKTOK_PAGE = "https://www.tiktok.com/@user/video/7643812958950362389"
TIKTOK_VIDEO = "https://v16m.tiktokcdn-us.com/abc/video.mp4"
TIKTOK_AUDIO = "https://sf16-ies-music.tiktokcdn.com/obj/track.mp3"
INSTAGRAM_PAGE = "https://www.instagram.com/reel/DbKcGl3t2iz/"
INSTAGRAM_VIDEO = "https://scontent-ams2-1.cdninstagram.com/o1/v/t2/reel.mp4"

pytestmark = pytest.mark.unit


def _fast_media(size: int, audio_url: str | None = TIKTOK_AUDIO) -> FastMedia:
    return FastMedia(
        video_url=TIKTOK_VIDEO,
        size=size,
        duration=11,
        title="ролик",
        audio_url=audio_url,
        audio_is_video_sound=audio_url is not None,
    )


@pytest.fixture(autouse=True)
def no_resolver_calls(monkeypatch):
    """Резолвер TikTok в тестах не должен ходить в сеть."""
    monkeypatch.setattr(
        tiktok_instagram_utils, "_resolve_tiktok_url", lambda url: url
    )


def test_tiktok_video_uses_size_from_the_resolver(monkeypatch):
    """Резолвер уже сообщил размер — лишнего запроса быть не должно."""
    monkeypatch.setattr(
        tiktok_instagram_utils, "fetch_tiktok_fast_media", lambda url: _fast_media(3 * MB)
    )
    probes = []
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "probe_remote_size",
        lambda url, referer=None: probes.append(url),
    )

    plan = tiktok_instagram_utils.resolve_tiktok_video_handoff(TIKTOK_PAGE)

    assert plan is not None
    assert plan.url == TIKTOK_VIDEO
    assert plan.size == 3 * MB
    assert probes == []


def test_tiktok_video_over_the_limit_is_refused(monkeypatch):
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "fetch_tiktok_fast_media",
        lambda url: _fast_media(25 * MB),
    )

    assert tiktok_instagram_utils.resolve_tiktok_video_handoff(TIKTOK_PAGE) is None


def test_tiktok_video_without_size_is_probed(monkeypatch):
    monkeypatch.setattr(
        tiktok_instagram_utils, "fetch_tiktok_fast_media", lambda url: _fast_media(0)
    )
    monkeypatch.setattr(
        tiktok_instagram_utils, "probe_remote_size", lambda url, referer=None: 2 * MB
    )

    plan = tiktok_instagram_utils.resolve_tiktok_video_handoff(TIKTOK_PAGE)

    assert plan is not None
    assert plan.size == 2 * MB


def test_unavailable_fast_path_means_no_handoff(monkeypatch):
    def _unavailable(url):
        raise FastPathUnavailable("резолвер отказал")

    monkeypatch.setattr(
        tiktok_instagram_utils, "fetch_tiktok_fast_media", _unavailable
    )

    assert tiktok_instagram_utils.resolve_tiktok_video_handoff(TIKTOK_PAGE) is None


def test_tiktok_audio_is_handed_over_when_it_is_the_video_sound(monkeypatch):
    monkeypatch.setattr(
        tiktok_instagram_utils, "fetch_tiktok_fast_media", lambda url: _fast_media(3 * MB)
    )
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "probe_remote_size",
        lambda url, referer=None: int(0.3 * MB),
    )

    plan = tiktok_instagram_utils.resolve_tiktok_audio_handoff(TIKTOK_PAGE)

    assert plan is not None
    assert plan.url == TIKTOK_AUDIO
    assert plan.kind == "audio"


def test_tiktok_audio_without_a_separate_link_is_refused(monkeypatch):
    """Лицензированный трек звуком видео не является — его вырезают из файла."""
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "fetch_tiktok_fast_media",
        lambda url: _fast_media(3 * MB, audio_url=None),
    )

    assert tiktok_instagram_utils.resolve_tiktok_audio_handoff(TIKTOK_PAGE) is None


def test_instagram_video_is_probed(monkeypatch):
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "fetch_instagram_fast_media",
        lambda url: InstagramFastMedia(video_url=INSTAGRAM_VIDEO, title="рилс"),
    )
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "probe_remote_size",
        lambda url, referer=None: 6 * MB,
    )

    plan = tiktok_instagram_utils.resolve_instagram_video_handoff(INSTAGRAM_PAGE)

    assert plan is not None
    assert plan.url == INSTAGRAM_VIDEO
    assert plan.size == 6 * MB


def test_instagram_video_without_size_is_refused(monkeypatch):
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "fetch_instagram_fast_media",
        lambda url: InstagramFastMedia(video_url=INSTAGRAM_VIDEO, title="рилс"),
    )
    monkeypatch.setattr(
        tiktok_instagram_utils, "probe_remote_size", lambda url, referer=None: None
    )

    assert tiktok_instagram_utils.resolve_instagram_video_handoff(INSTAGRAM_PAGE) is None


# --- сам замер размера -----------------------------------------------------


def test_probe_reads_total_size_from_content_range(monkeypatch):
    """У Range-ответа честный размер лежит в Content-Range, не в Content-Length."""
    monkeypatch.setattr(
        tiktok_instagram_utils.httpx,
        "get",
        lambda url, **kwargs: SimpleNamespace(
            status_code=206,
            headers={"content-range": "bytes 0-0/6291456", "content-length": "1"},
        ),
    )

    assert tiktok_instagram_utils.probe_remote_size(TIKTOK_VIDEO) == 6291456


def test_probe_falls_back_to_content_length(monkeypatch):
    monkeypatch.setattr(
        tiktok_instagram_utils.httpx,
        "get",
        lambda url, **kwargs: SimpleNamespace(
            status_code=200, headers={"content-length": "2097152"}
        ),
    )

    assert tiktok_instagram_utils.probe_remote_size(TIKTOK_VIDEO) == 2097152


def test_probe_returns_nothing_on_network_error(monkeypatch):
    def _boom(url, **kwargs):
        raise tiktok_instagram_utils.httpx.ConnectError("нет связи")

    monkeypatch.setattr(tiktok_instagram_utils.httpx, "get", _boom)

    assert tiktok_instagram_utils.probe_remote_size(TIKTOK_VIDEO) is None


def test_probe_ignores_error_responses(monkeypatch):
    """VK отдаёт 400 с двумя байтами тела — принимать это за размер нельзя."""
    monkeypatch.setattr(
        tiktok_instagram_utils.httpx,
        "get",
        lambda url, **kwargs: SimpleNamespace(
            status_code=400, headers={"content-length": "2"}
        ),
    )

    assert tiktok_instagram_utils.probe_remote_size(TIKTOK_VIDEO) is None
