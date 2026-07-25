"""Тесты переиспользования ответа резолвера TikTok.

Резолвер отвечает «Free Api Limit: 1 request/second» — проверено на живых
вызовах: второй запрос по той же ссылке подряд гарантированно отказывает.
Между решением «отдать ссылкой» и скачиванием файла проходят миллисекунды,
поэтому ответ обязан переиспользоваться, иначе откат на скачивание сам себя
ломает.
"""

from types import SimpleNamespace

import pytest

from utils import tiktok_instagram_utils


PAYLOAD = {
    "code": 0,
    "data": {
        "play": "https://v16m.tiktokcdn-us.com/abc/video.mp4",
        "size": 2 * 1024 * 1024,
        "duration": 11,
        "title": "ролик",
    },
}
FIRST = "https://www.tiktok.com/@user/video/1"
SECOND = "https://www.tiktok.com/@user/video/2"

pytestmark = pytest.mark.unit


@pytest.fixture
def resolver_calls(monkeypatch):
    """Считает обращения к резолверу и обнуляет память между тестами."""
    calls: list[str] = []

    def _get(api_url, params=None, **kwargs):
        calls.append((params or {}).get("url", ""))
        return SimpleNamespace(
            raise_for_status=lambda: None, json=lambda: PAYLOAD
        )

    monkeypatch.setattr(tiktok_instagram_utils.httpx, "get", _get)
    tiktok_instagram_utils.reset_tiktok_resolver_memo()
    yield calls
    tiktok_instagram_utils.reset_tiktok_resolver_memo()


def test_second_call_for_the_same_link_reuses_the_answer(resolver_calls):
    first = tiktok_instagram_utils.fetch_tiktok_fast_media(FIRST)
    second = tiktok_instagram_utils.fetch_tiktok_fast_media(FIRST)

    assert resolver_calls == [FIRST]
    assert first == second


def test_different_links_are_resolved_separately(resolver_calls):
    tiktok_instagram_utils.fetch_tiktok_fast_media(FIRST)
    tiktok_instagram_utils.fetch_tiktok_fast_media(SECOND)

    assert resolver_calls == [FIRST, SECOND]


def test_answer_is_reasked_after_it_goes_stale(resolver_calls, monkeypatch):
    """Ссылки резолвера подписаны на часы, но держать их вечно незачем."""
    clock = iter([0.0, 0.0, 1000.0, 1000.0])
    monkeypatch.setattr(
        tiktok_instagram_utils.time, "monotonic", lambda: next(clock)
    )

    tiktok_instagram_utils.fetch_tiktok_fast_media(FIRST)
    tiktok_instagram_utils.fetch_tiktok_fast_media(FIRST)

    assert resolver_calls == [FIRST, FIRST]


def test_failed_answer_is_not_remembered(resolver_calls, monkeypatch):
    """Отказ резолвера кэшировать нельзя — иначе он залипнет на всю память."""
    failures = {"code": -1, "msg": "Free Api Limit: 1 request/second."}
    monkeypatch.setattr(
        tiktok_instagram_utils.httpx,
        "get",
        lambda api_url, params=None, **kwargs: SimpleNamespace(
            raise_for_status=lambda: None, json=lambda: failures
        ),
    )

    with pytest.raises(Exception):
        tiktok_instagram_utils.fetch_tiktok_fast_media(FIRST)

    monkeypatch.setattr(
        tiktok_instagram_utils.httpx,
        "get",
        lambda api_url, params=None, **kwargs: SimpleNamespace(
            raise_for_status=lambda: None, json=lambda: PAYLOAD
        ),
    )

    assert tiktok_instagram_utils.fetch_tiktok_fast_media(FIRST).size == 2 * 1024 * 1024
