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
        template = self.options["outtmpl"] if "outtmpl" in self.options else "% (title)s.%(ext)s"
        path_str = template.replace("%(title)s", info["title"]).replace("%(ext)s", info["ext"])
        return Path(path_str)


@pytest.fixture(autouse=True)
def patch_cookies(monkeypatch):
    """Ensure tests do not depend on local cookie files."""
    monkeypatch.setattr(youtube_utils.Path, "is_file", lambda *args, **kwargs: False, raising=False)


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


def test_get_video_info_uses_cookiefile_option(monkeypatch):
    captured_options = []

    class CapturingYDL(FakeYDL):
        def __init__(self, options):
            captured_options.append(options.copy())
            super().__init__(options)

    monkeypatch.setattr(youtube_utils.yt_dlp, "YoutubeDL", CapturingYDL)
    monkeypatch.setattr(youtube_utils, "YOUTUBE_COOKIES_FILE", "cookies.txt")
    monkeypatch.setattr(youtube_utils.Path, "is_file", lambda *args, **kwargs: True, raising=False)

    youtube_utils.get_video_info("https://youtu.be/abc123def45")

    assert captured_options, "YoutubeDL options were not captured"
    assert captured_options[0].get("cookiefile") == "cookies.txt"
    assert "cookies" not in captured_options[0]
