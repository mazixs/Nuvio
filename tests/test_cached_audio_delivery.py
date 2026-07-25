"""Тесты доставки аудио из кэша file_id."""

import asyncio
import typing
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import telegram

from utils import telegram_utils


@pytest.mark.unit
def test_deliver_cached_audio_annotates_query():
    """Аннотация query должна совпадать с образцом send_single_file."""
    hints = typing.get_type_hints(telegram_utils._deliver_cached_audio)

    assert hints["query"] is telegram.CallbackQuery


def _query(reply_audio):
    return SimpleNamespace(message=SimpleNamespace(reply_audio=reply_audio))


@pytest.mark.unit
def test_cached_audio_is_delivered_by_file_id(monkeypatch):
    reply_audio = AsyncMock()
    monkeypatch.setattr(
        telegram_utils.telegram_cache,
        "get",
        lambda url, format_id: SimpleNamespace(file_id="AGADcached"),
    )

    delivered = asyncio.run(
        telegram_utils._deliver_cached_audio(
            _query(reply_audio), "https://example.test/v", "tiktok_audio"
        )
    )

    assert delivered is True
    assert reply_audio.await_args.kwargs["audio"] == "AGADcached"


@pytest.mark.unit
def test_missing_cache_entry_reports_not_delivered(monkeypatch):
    reply_audio = AsyncMock()
    monkeypatch.setattr(
        telegram_utils.telegram_cache, "get", lambda url, format_id: None
    )

    delivered = asyncio.run(
        telegram_utils._deliver_cached_audio(
            _query(reply_audio), "https://example.test/v", "tiktok_audio"
        )
    )

    assert delivered is False
    reply_audio.assert_not_awaited()


@pytest.mark.unit
def test_stale_file_id_is_dropped_from_cache(monkeypatch):
    """Устаревший file_id должен удаляться, чтобы не отдаваться повторно."""
    reply_audio = AsyncMock(side_effect=telegram.error.BadRequest("wrong file_id"))
    deleted: list[str] = []
    monkeypatch.setattr(
        telegram_utils.telegram_cache,
        "get",
        lambda url, format_id: SimpleNamespace(file_id="AGADstale"),
    )
    monkeypatch.setattr(
        telegram_utils.telegram_cache,
        "delete_by_file_id",
        lambda file_id: deleted.append(file_id),
    )

    delivered = asyncio.run(
        telegram_utils._deliver_cached_audio(
            _query(reply_audio), "https://example.test/v", "tiktok_audio"
        )
    )

    assert delivered is False
    assert deleted == ["AGADstale"]
