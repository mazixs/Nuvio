#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Integration tests for TelegramVideoCache."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from utils.video_cache import TelegramVideoCache, CachedVideo


@pytest.fixture()
def cache_db(tmp_path: Path) -> TelegramVideoCache:
    return TelegramVideoCache(db_path=tmp_path / "telegram_cache_test.db")


def _sample_cached(url: str = "https://example.com/video", unique_id: str = "unique123") -> CachedVideo:
    return CachedVideo(
        url=url,
        file_id="file123",
        file_unique_id=unique_id,
        platform="youtube",
        format_id="best",
        cached_at=datetime.now(),
        file_size=100,
        duration=60,
        title="Sample",
    )


def test_set_and_get_roundtrip(cache_db: TelegramVideoCache):
    cached = _sample_cached()
    cache_db.set(cached)

    loaded = cache_db.get(cached.url, cached.format_id)
    assert loaded is not None
    assert loaded.file_id == cached.file_id
    assert loaded.title == cached.title


def test_delete_removes_entry(cache_db: TelegramVideoCache):
    cached = _sample_cached()
    cache_db.set(cached)
    cache_db.delete(cached.url, cached.format_id)
    assert cache_db.get(cached.url, cached.format_id) is None


def test_cleanup_expired(cache_db: TelegramVideoCache):
    old_entry = _sample_cached("https://old", unique_id="unique_old")
    old_entry.cached_at = datetime.now() - timedelta(days=200)
    fresh_entry = _sample_cached("https://fresh", unique_id="unique_new")

    cache_db.set(old_entry)
    cache_db.set(fresh_entry)

    deleted = cache_db.cleanup_expired(ttl_days=90)
    assert deleted == 1
    assert cache_db.get("https://old") is None
    assert cache_db.get("https://fresh") is not None


def test_vacuum_executes(cache_db: TelegramVideoCache):
    cache_db.vacuum()
    # Success is absence of exceptions and file exists
    assert cache_db.db_path.exists()
