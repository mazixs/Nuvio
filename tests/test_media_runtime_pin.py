"""Проверка чтения версии yt-dlp из lock-файла образа."""

import pytest

from scripts.check_media_runtime import _locked_ytdlp_version


@pytest.mark.unit
@pytest.mark.parametrize(
    "line",
    [
        "yt-dlp==2026.9.16.232951.dev0\n",
        "yt-dlp[default]==2026.9.16.232951.dev0\n",
    ],
)
def test_locked_ytdlp_version_accepts_lock_formats(line):
    assert _locked_ytdlp_version(line) == "2026.9.16.232951.dev0"


@pytest.mark.unit
@pytest.mark.parametrize(
    "lock_text",
    [
        "httpx==0.28.1\n",
        "yt-dlp==1.0\nyt-dlp[default]==2.0\n",
    ],
)
def test_locked_ytdlp_version_rejects_missing_or_duplicate_pin(lock_text):
    with pytest.raises(RuntimeError, match="ровно один pin"):
        _locked_ytdlp_version(lock_text)
