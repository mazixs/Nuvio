"""Тесты отправки файлов через локальный Telegram Bot API."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from utils import telegram_utils, ytdlp_common


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.unit
def test_finalize_returns_local_path_below_limit(monkeypatch, tmp_path):
    media = tmp_path / "video.mp4"
    media.write_bytes(b"x" * 10)
    monkeypatch.setattr(ytdlp_common, "MAX_FILE_SIZE", 20)

    assert ytdlp_common.finalize_downloaded_file(media, False) == media


@pytest.mark.unit
def test_finalize_deletes_and_rejects_file_above_limit(monkeypatch, tmp_path):
    media = tmp_path / "video.mp4"
    media.write_bytes(b"x" * 21)
    monkeypatch.setattr(ytdlp_common, "MAX_FILE_SIZE", 20)

    with pytest.raises(ytdlp_common.FileSizeLimitError):
        ytdlp_common.finalize_downloaded_file(media, False)

    assert not media.exists()


@pytest.mark.unit
def test_send_single_file_passes_absolute_path_to_local_api(monkeypatch, tmp_path):
    media = tmp_path / "video.mp4"
    media.write_bytes(b"video")
    sent_message = SimpleNamespace(video=None, audio=None, document=None)
    reply_video = AsyncMock(return_value=sent_message)
    query = SimpleNamespace(
        message=SimpleNamespace(reply_video=reply_video),
        edit_message_text=AsyncMock(),
    )
    monkeypatch.setattr(
        telegram_utils, "TELEGRAM_LOCAL_MODE", True, raising=False
    )

    result = asyncio.run(
        telegram_utils.send_single_file(
            query,
            media,
            "session-token",
            {"platform": "tiktok", "url": "https://example.test/video"},
            max_retries=1,
        )
    )

    assert result is True
    assert reply_video.await_args.kwargs["video"] == media.resolve()


@pytest.mark.unit
def test_runtime_has_no_gokapi_dependency():
    runtime_files = [
        PROJECT_ROOT / "config.py",
        PROJECT_ROOT / "messages.py",
        *(PROJECT_ROOT / "utils").glob("*.py"),
    ]

    assert not (PROJECT_ROOT / "utils" / "gokapi_utils.py").exists()
    for runtime_file in runtime_files:
        assert "gokapi" not in runtime_file.read_text(encoding="utf-8").lower(), (
            f"Осталась зависимость Gokapi: {runtime_file}"
        )
