"""Тесты памяти отказов CDN на доставку ссылкой.

Проверено на живом сервере: ссылка TikTok на видео отдаётся любому клиенту
(HTTP 200, `video/mp4`), но Telegram её забрать не может — CDN отказывает именно
его инфраструктуре, 6 попыток из 6. При этом картинки того же TikTok и видео
Instagram уходят нормально. Значит бесполезно повторять попытку для того же
сочетания «домен + вид медиа», пока отказ свеж, — но и запрещать её навсегда
нельзя: политика CDN меняется.
"""

import pytest

from utils.url_delivery import HandoffRefusals


TIKTOK_VIDEO = "https://v16m.tiktokcdn-us.com/abc/video.mp4"
TIKTOK_OTHER_EDGE = "https://v19m.tiktokcdn-us.com/xyz/video.mp4"
TIKTOK_IMAGE = "https://p16-sign.tiktokcdn-us.com/obj/image.jpeg"
INSTAGRAM_VIDEO = "https://scontent-ams2-1.cdninstagram.com/o1/v/t2/reel.mp4"

pytestmark = pytest.mark.unit


def test_fresh_memory_blocks_nothing():
    assert HandoffRefusals().is_cooling_down(TIKTOK_VIDEO, "video", now=0.0) is False


def test_refusal_stops_repeating_the_same_attempt():
    refusals = HandoffRefusals()
    refusals.remember(TIKTOK_VIDEO, "video", now=100.0)

    assert refusals.is_cooling_down(TIKTOK_VIDEO, "video", now=100.0) is True


def test_other_edge_of_the_same_cdn_is_also_skipped():
    """CDN отдаёт видео с разных краёв; отказ относится к политике, не к хосту."""
    refusals = HandoffRefusals()
    refusals.remember(TIKTOK_VIDEO, "video", now=100.0)

    assert refusals.is_cooling_down(TIKTOK_OTHER_EDGE, "video", now=100.0) is True


def test_pictures_of_the_same_cdn_keep_working():
    """Измерено: картинки TikTok уходили ссылкой, когда видео уже отказывало."""
    refusals = HandoffRefusals()
    refusals.remember(TIKTOK_VIDEO, "video", now=100.0)

    assert refusals.is_cooling_down(TIKTOK_IMAGE, "photo", now=100.0) is False


def test_another_cdn_is_not_punished():
    refusals = HandoffRefusals()
    refusals.remember(TIKTOK_VIDEO, "video", now=100.0)

    assert refusals.is_cooling_down(INSTAGRAM_VIDEO, "video", now=100.0) is False


def test_memory_fades_so_the_cdn_gets_another_chance():
    refusals = HandoffRefusals()
    refusals.remember(TIKTOK_VIDEO, "video", now=100.0)
    later = 100.0 + refusals.cooldown_seconds + 1

    assert refusals.is_cooling_down(TIKTOK_VIDEO, "video", now=later) is False


def test_unknown_host_is_handled_without_crashing():
    refusals = HandoffRefusals()
    refusals.remember("не ссылка", "video", now=100.0)

    assert refusals.is_cooling_down("не ссылка", "video", now=100.0) is False
