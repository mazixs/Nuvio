"""Тесты скачивания TikTok по быстрому пути и откатa на yt-dlp."""

from pathlib import Path

import pytest

from utils import tiktok_instagram_utils
from utils.tiktok_fast_path import FastPathUnavailable


def _resolver_payload(**music_info_overrides):
    music_info = {
        "title": "original sound - tester",
        "duration": 60,
        "original": True,
        "play": "https://cdn.example.test/music.mp3",
    }
    music_info.update(music_info_overrides)
    return {
        "code": 0,
        "msg": "success",
        "data": {
            "id": "7639073022762175764",
            "title": "Клип",
            "duration": 60,
            "size": 5576522,
            "play": "https://cdn.example.test/play.mp4",
            "music": "https://cdn.example.test/music.mp3",
            "cover": "https://cdn.example.test/cover.jpg",
            "author": {"unique_id": "tester"},
            "music_info": music_info,
        },
    }


@pytest.fixture
def fake_downloads(monkeypatch):
    """Подменяет сетевое скачивание записью заглушки на диск."""
    requested: list[str] = []

    def _fake_download(url, destination, referer=None):
        requested.append(url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"media")
        return destination

    monkeypatch.setattr(
        tiktok_instagram_utils, "_download_remote_file", _fake_download
    )
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_resolve_tiktok_url",
        lambda url: "https://www.tiktok.com/@tester/video/7639073022762175764",
    )
    return requested


@pytest.mark.unit
def test_fetch_tiktok_fast_media_parses_resolver_response(monkeypatch):
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_call_tiktok_resolver",
        lambda url: _resolver_payload(),
    )

    media = tiktok_instagram_utils.fetch_tiktok_fast_media(
        "https://vt.tiktok.com/example/"
    )

    assert media.video_url == "https://cdn.example.test/play.mp4"
    assert media.audio_is_video_sound is True


@pytest.mark.unit
def test_fast_video_download_uses_direct_url(monkeypatch, tmp_path, fake_downloads):
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_call_tiktok_resolver",
        lambda url: _resolver_payload(),
    )

    result = tiktok_instagram_utils.download_tiktok_video_fast(
        "https://vt.tiktok.com/example/",
        "session-fast",
        output_dir=tmp_path,
    )

    assert Path(result).exists()
    assert fake_downloads == ["https://cdn.example.test/play.mp4"]


@pytest.mark.unit
def test_fast_audio_uses_music_url_for_original_sound(
    monkeypatch, tmp_path, fake_downloads
):
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_call_tiktok_resolver",
        lambda url: _resolver_payload(),
    )

    result = tiktok_instagram_utils.download_tiktok_audio_fast(
        "https://vt.tiktok.com/example/",
        "session-fast",
        output_dir=tmp_path,
    )

    assert Path(result).exists()
    assert fake_downloads == ["https://cdn.example.test/music.mp3"]


@pytest.mark.unit
def test_fast_audio_extracts_from_video_for_licensed_track(
    monkeypatch, tmp_path, fake_downloads
):
    """Лицензированный трек нельзя отдавать вместо звука видео."""
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_call_tiktok_resolver",
        lambda url: _resolver_payload(
            title="SAVAGE", duration=168, original=False
        ),
    )
    extracted: list[Path] = []

    def _fake_extract(input_path, session_id, output_filename=None):
        extracted.append(input_path)
        target = tmp_path / "extracted.m4a"
        target.write_bytes(b"audio")
        return target

    monkeypatch.setattr(
        tiktok_instagram_utils, "extract_audio_copy", _fake_extract
    )

    result = tiktok_instagram_utils.download_tiktok_audio_fast(
        "https://vt.tiktok.com/example/",
        "session-fast",
        output_dir=tmp_path,
    )

    assert fake_downloads == ["https://cdn.example.test/play.mp4"]
    assert len(extracted) == 1
    assert Path(result).name == "extracted.m4a"


@pytest.mark.unit
def test_download_tiktok_video_falls_back_to_ytdlp(monkeypatch, fake_downloads):
    """Отказ быстрого пути не должен ломать скачивание."""

    def _unavailable(*args, **kwargs):
        raise FastPathUnavailable("резолвер недоступен")

    monkeypatch.setattr(
        tiktok_instagram_utils, "TIKTOK_FAST_PATH", True, raising=False
    )
    monkeypatch.setattr(
        tiktok_instagram_utils, "download_tiktok_video_fast", _unavailable
    )
    monkeypatch.setattr(
        tiktok_instagram_utils, "_get_tiktok_base_configs", lambda: [{}]
    )
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_smart_retry",
        lambda func, max_attempts=2, context="": Path("/tmp/ytdlp-fallback.mp4"),
    )

    result = tiktok_instagram_utils.download_tiktok_video(
        "https://vt.tiktok.com/example/", "session-fallback"
    )

    assert result == Path("/tmp/ytdlp-fallback.mp4")


@pytest.mark.unit
def test_download_tiktok_video_skips_fast_path_when_disabled(
    monkeypatch, fake_downloads
):
    calls: list[str] = []

    def _record(*args, **kwargs):
        calls.append("fast")
        return Path("/tmp/fast.mp4")

    monkeypatch.setattr(
        tiktok_instagram_utils, "TIKTOK_FAST_PATH", False, raising=False
    )
    monkeypatch.setattr(
        tiktok_instagram_utils, "download_tiktok_video_fast", _record
    )
    monkeypatch.setattr(
        tiktok_instagram_utils, "_get_tiktok_base_configs", lambda: [{}]
    )
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_smart_retry",
        lambda func, max_attempts=2, context="": Path("/tmp/ytdlp.mp4"),
    )

    result = tiktok_instagram_utils.download_tiktok_video(
        "https://vt.tiktok.com/example/", "session-disabled"
    )

    assert calls == []
    assert result == Path("/tmp/ytdlp.mp4")
