#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Smoke tests for rutube_vk_utils with mocked yt-dlp."""

from pathlib import Path

from utils import rutube_vk_utils


class FakeYDL:
    """Minimal yt-dlp stub to avoid real network calls."""

    def __init__(self, options):
        self.options = options
        self._info = {
            "id": "abc123",
            "title": "smoke_video",
            "ext": "mp4",
            "duration": 60,
            "uploader": "tester",
            "formats": [
                {
                    "format_id": "0",
                    "ext": "mp4",
                    "height": 720,
                    "width": 1280,
                    "vcodec": "h264",
                    "acodec": "aac",
                    "filesize": 10_000_000,
                },
                {
                    "format_id": "1",
                    "ext": "m4a",
                    "vcodec": "none",
                    "acodec": "aac",
                    "audio_channels": 2,
                    "filesize": 2_000_000,
                },
            ],
        }

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def extract_info(self, url, download=False):
        self.last_url = url
        if download:
            output_path = self._resolve_output_path()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("stub video content")
        return self._info

    def prepare_filename(self, info):
        return str(self._resolve_output_path(info))

    def _resolve_output_path(self, info=None):
        info = info or self._info
        template = self.options.get("outtmpl", "%(title)s.%(ext)s")
        path_str = template.replace("%(title)s", info["title"]).replace("%(ext)s", info["ext"])
        return Path(path_str)


def test_is_valid_rutube_url():
    assert rutube_vk_utils.is_valid_rutube_url("https://rutube.ru/video/abc123/")
    assert rutube_vk_utils.is_valid_rutube_url("https://rutube.ru/video/abc123")
    assert rutube_vk_utils.is_valid_rutube_url("https://rutu.be/abc123")
    assert not rutube_vk_utils.is_valid_rutube_url("https://youtube.com/watch?v=abc123")


def test_is_valid_vk_url():
    assert rutube_vk_utils.is_valid_vk_url("https://vk.com/video-12345_67890")
    assert rutube_vk_utils.is_valid_vk_url("https://vkvideo.ru/video-12345_67890")
    assert rutube_vk_utils.is_valid_vk_url("https://vk.com/clip-12345_67890")
    assert rutube_vk_utils.is_valid_vk_url("https://vk.com/wall-12345_67890")
    assert not rutube_vk_utils.is_valid_vk_url("https://youtube.com/watch?v=abc123")


def test_get_rutube_info_smoke_without_network(monkeypatch):
    monkeypatch.setattr(rutube_vk_utils.yt_dlp, "YoutubeDL", FakeYDL)
    info = rutube_vk_utils.get_rutube_info("https://rutube.ru/video/abc123/")
    assert info["title"] == "smoke_video"
    assert info["duration"] == 60


def test_get_vk_info_smoke_without_network(monkeypatch):
    monkeypatch.setattr(rutube_vk_utils.yt_dlp, "YoutubeDL", FakeYDL)
    info = rutube_vk_utils.get_vk_info("https://vk.com/video-12345_67890")
    assert info["title"] == "smoke_video"
    assert info["duration"] == 60


def test_download_rutube_video_smoke_returns_local_file(monkeypatch, tmp_path):
    monkeypatch.setattr(rutube_vk_utils.yt_dlp, "YoutubeDL", FakeYDL)
    result = rutube_vk_utils.download_rutube_video(
        "https://rutube.ru/video/abc123/",
        session_id="smoke",
        output_dir=tmp_path,
        force_local=True,
    )
    assert isinstance(result, Path)
    assert result.exists()
    assert result.read_text() == "stub video content"


def test_download_vk_video_smoke_returns_local_file(monkeypatch, tmp_path):
    monkeypatch.setattr(rutube_vk_utils.yt_dlp, "YoutubeDL", FakeYDL)
    result = rutube_vk_utils.download_vk_video(
        "https://vk.com/video-12345_67890",
        session_id="smoke",
        output_dir=tmp_path,
        force_local=True,
    )
    assert isinstance(result, Path)
    assert result.exists()
    assert result.read_text() == "stub video content"


def test_get_available_formats_rutube_groups_formats(monkeypatch):
    monkeypatch.setattr(rutube_vk_utils.yt_dlp, "YoutubeDL", FakeYDL)
    info = rutube_vk_utils.get_rutube_info("https://rutube.ru/video/abc123/")
    formats = rutube_vk_utils.get_available_formats_rutube(info)
    assert len(formats["combined"]) == 1
    assert len(formats["audio_only"]) == 1
    assert formats["combined"][0]["height"] == 720


def test_get_available_formats_vk_groups_formats(monkeypatch):
    monkeypatch.setattr(rutube_vk_utils.yt_dlp, "YoutubeDL", FakeYDL)
    info = rutube_vk_utils.get_vk_info("https://vk.com/video-12345_67890")
    formats = rutube_vk_utils.get_available_formats_vk(info)
    assert len(formats["combined"]) == 1
    assert len(formats["audio_only"]) == 1
