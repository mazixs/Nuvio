#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Smoke tests for youtube_utils with mocked yt-dlp."""

from pathlib import Path

import pytest

from utils import youtube_utils


class FakeYDL:
    """Minimal yt-dlp stub to avoid real network calls."""

    def __init__(self, options):
        self.options = options
        self._info = {
            "id": "abc123def45",
            "title": "smoke_video",
            "ext": "mp4",
            "duration": 60,
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
        template = (
            self.options["outtmpl"]
            if "outtmpl" in self.options
            else "% (title)s.%(ext)s"
        )
        path_str = template.replace("%(title)s", info["title"]).replace(
            "%(ext)s", info["ext"]
        )
        return Path(path_str)


@pytest.fixture(autouse=True)
def patch_cookies(monkeypatch):
    """Ensure tests do not depend on local cookie files."""
    monkeypatch.setattr(
        youtube_utils.Path, "is_file", lambda *args, **kwargs: False, raising=False
    )


def test_get_video_info_smoke_without_network(monkeypatch):
    monkeypatch.setattr(youtube_utils.yt_dlp, "YoutubeDL", FakeYDL)
    info = youtube_utils.get_video_info("https://youtu.be/abc123def45")
    assert info["title"] == "smoke_video"
    assert info["duration"] == 60


def test_download_video_smoke_returns_local_file(monkeypatch, tmp_path):
    monkeypatch.setattr(youtube_utils.yt_dlp, "YoutubeDL", FakeYDL)
    result = youtube_utils.download_video(
        "https://youtu.be/abc123def45",
        "best",
        session_id="smoke",
        output_dir=tmp_path,
        force_local=True,
    )
    assert isinstance(result, Path)
    assert result.exists()
    assert result.read_text() == "stub video content"


def _capture_options(monkeypatch, ydl_class):
    """Включает cookie-файл и подменяет YoutubeDL на перехватывающий вариант."""
    monkeypatch.setattr(youtube_utils.yt_dlp, "YoutubeDL", ydl_class)
    monkeypatch.setattr(youtube_utils, "YOUTUBE_COOKIES_FILE", "cookies.txt")
    monkeypatch.setattr(
        youtube_utils.Path, "is_file", lambda *args, **kwargs: True, raising=False
    )


def test_get_video_info_tries_without_cookies_first(monkeypatch):
    """Анонимный запрос идёт первым: только ему достаётся клиент без PO-токена."""
    captured_options = []

    class CapturingYDL(FakeYDL):
        def __init__(self, options):
            captured_options.append(options.copy())
            super().__init__(options)

    _capture_options(monkeypatch, CapturingYDL)

    youtube_utils.get_video_info("https://youtu.be/abc123def45")

    assert len(captured_options) == 1, "лишняя попытка с cookies"
    assert "cookiefile" not in captured_options[0]


def test_get_video_info_falls_back_to_cookies(monkeypatch):
    """Cookies уходят во вторую попытку — для видео с ограничениями."""
    captured_options = []

    class AnonFailingYDL(FakeYDL):
        def __init__(self, options):
            captured_options.append(options.copy())
            super().__init__(options)

        def extract_info(self, url, download=False):
            if "cookiefile" not in self.options:
                raise youtube_utils.yt_dlp.utils.DownloadError("Private video")
            return super().extract_info(url, download=download)

    _capture_options(monkeypatch, AnonFailingYDL)

    info = youtube_utils.get_video_info("https://youtu.be/abc123def45")

    assert info["title"] == "smoke_video"
    assert len(captured_options) == 2
    assert "cookiefile" not in captured_options[0]
    assert captured_options[1].get("cookiefile") == "cookies.txt"
    assert "cookies" not in captured_options[1]


def test_download_video_tries_without_cookies_first(monkeypatch, tmp_path):
    """Порядок у видео тот же, что у аудио: без cookies — первым."""
    captured_options = []

    class CapturingYDL(FakeYDL):
        def __init__(self, options):
            captured_options.append(options.copy())
            super().__init__(options)

    _capture_options(monkeypatch, CapturingYDL)

    youtube_utils.download_video(
        "https://youtu.be/abc123def45",
        "best",
        session_id="order",
        output_dir=tmp_path,
        force_local=True,
    )

    assert len(captured_options) == 1
    assert "cookiefile" not in captured_options[0]
