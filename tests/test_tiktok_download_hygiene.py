"""Тесты экономии запросов и выбора аудиоформатов TikTok."""

from types import SimpleNamespace

import httpx
import pytest

from utils import tiktok_instagram_utils


RESOLVED = "https://www.tiktok.com/@tester/video/7639073022762175764"
SHORT = "https://vt.tiktok.com/ZSxeYGgGC/"


# === развёртывание короткой ссылки ===


@pytest.mark.unit
def test_resolve_tiktok_url_does_not_download_page_body(monkeypatch):
    """Для редиректа достаточно заголовков, тело страницы — лишний трафик."""
    calls: list[str] = []

    def _head(url, **kwargs):
        calls.append("head")
        return SimpleNamespace(url=RESOLVED, status_code=200)

    def _get(url, **kwargs):
        calls.append("get")
        return SimpleNamespace(url=RESOLVED, status_code=200)

    monkeypatch.setattr(tiktok_instagram_utils.httpx, "head", _head)
    monkeypatch.setattr(tiktok_instagram_utils.httpx, "get", _get)

    assert tiktok_instagram_utils._resolve_tiktok_url(SHORT) == RESOLVED
    assert calls == ["head"]


@pytest.mark.unit
def test_resolve_tiktok_url_falls_back_to_get_when_head_fails(monkeypatch):
    """Часть серверов отвечает на HEAD ошибкой — тогда нужен обычный запрос."""
    calls: list[str] = []

    def _head(url, **kwargs):
        calls.append("head")
        raise httpx.HTTPError("HEAD не поддерживается")

    def _get(url, **kwargs):
        calls.append("get")
        return SimpleNamespace(url=RESOLVED, status_code=200)

    monkeypatch.setattr(tiktok_instagram_utils.httpx, "head", _head)
    monkeypatch.setattr(tiktok_instagram_utils.httpx, "get", _get)

    assert tiktok_instagram_utils._resolve_tiktok_url(SHORT) == RESOLVED
    assert calls == ["head", "get"]


@pytest.mark.unit
def test_resolve_tiktok_url_falls_back_when_head_status_is_error(monkeypatch):
    calls: list[str] = []

    def _head(url, **kwargs):
        calls.append("head")
        return SimpleNamespace(url=SHORT, status_code=405)

    def _get(url, **kwargs):
        calls.append("get")
        return SimpleNamespace(url=RESOLVED, status_code=200)

    monkeypatch.setattr(tiktok_instagram_utils.httpx, "head", _head)
    monkeypatch.setattr(tiktok_instagram_utils.httpx, "get", _get)

    assert tiktok_instagram_utils._resolve_tiktok_url(SHORT) == RESOLVED
    assert calls == ["head", "get"]


@pytest.mark.unit
def test_resolve_tiktok_url_returns_original_when_both_requests_fail(monkeypatch):
    def _fail(url, **kwargs):
        raise httpx.HTTPError("сеть недоступна")

    monkeypatch.setattr(tiktok_instagram_utils.httpx, "head", _fail)
    monkeypatch.setattr(tiktok_instagram_utils.httpx, "get", _fail)

    assert tiktok_instagram_utils._resolve_tiktok_url(SHORT) == SHORT


# === выбор аудиоформата в запасном пути через yt-dlp ===


@pytest.mark.unit
def test_audio_format_sort_prefers_lower_bitrate():
    """Видеодорожка всё равно выбрасывается, поэтому лёгкий файл выгоднее."""
    light = {"format_id": "light", "tbr": 100, "filesize": 1000, "vcodec": "h264"}
    heavy = {"format_id": "heavy", "tbr": 900, "filesize": 9000, "vcodec": "h264"}

    ordered = sorted(
        [heavy, light],
        key=tiktok_instagram_utils._audio_format_sort_key,
        reverse=True,
    )

    assert [f["format_id"] for f in ordered] == ["light", "heavy"]


@pytest.mark.unit
def test_audio_format_sort_prefers_audio_only_among_equal_candidates():
    """При равных битрейте и размере чистое аудио предпочтительнее muxed."""
    muxed = {"format_id": "muxed", "tbr": 100, "filesize": 1000, "vcodec": "h264"}
    audio_only = {"format_id": "audio", "tbr": 100, "filesize": 1000, "vcodec": "none"}

    ordered = sorted(
        [muxed, audio_only],
        key=tiktok_instagram_utils._audio_format_sort_key,
        reverse=True,
    )

    assert [f["format_id"] for f in ordered] == ["audio", "muxed"]
