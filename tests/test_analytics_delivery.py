"""Старые запросы и подтвержденные отправки считаются отдельно."""

import pytest
from datetime import UTC, datetime, timedelta
import sqlite3

from utils import analytics_db


@pytest.mark.integration
def test_legacy_requests_are_not_relabelled_as_deliveries(tmp_path, monkeypatch):
    analytics_db.close_connection()
    monkeypatch.setattr(analytics_db, "_DB_PATH", tmp_path / "analytics.db")
    try:
        analytics_db.init_db()
        analytics_db.track_user(1)
        analytics_db.track_event(1, "download", platform="youtube")

        assert analytics_db.total_downloads() == 1
        assert analytics_db.total_deliveries() == 0

        analytics_db.track_event(1, "delivery", platform="youtube")
        assert analytics_db.total_deliveries() == 1
    finally:
        analytics_db.close_connection()


@pytest.mark.integration
def test_old_urls_are_pruned_after_verified_backup(tmp_path, monkeypatch):
    analytics_db.close_connection()
    monkeypatch.setattr(analytics_db, "_DB_PATH", tmp_path / "analytics.db")
    try:
        analytics_db.init_db()
        analytics_db.track_user(1)
        analytics_db.track_event(1, "download", url="https://example.com/old")
        analytics_db.track_event(1, "delivery", url="https://example.com/new")
        old = (datetime.now(UTC) - timedelta(days=100)).isoformat()
        with analytics_db._cursor_write() as cur:
            cur.execute("UPDATE events SET ts = ? WHERE event = 'download'", (old,))

        removed, backup = analytics_db.prune_old_event_urls(days=90, batch_size=1)

        assert removed == 1
        assert backup is not None
        with sqlite3.connect(str(backup)) as before:
            assert before.execute("SELECT url FROM events WHERE event = 'download'").fetchone()[0] == "https://example.com/old"
        with analytics_db._cursor_read() as cur:
            cur.execute("SELECT event, url FROM events ORDER BY id")
            assert [(row["event"], row["url"]) for row in cur] == [
                ("download", None),
                ("delivery", "https://example.com/new"),
            ]
        assert analytics_db.total_downloads() == 1
        assert analytics_db.total_deliveries() == 1
    finally:
        analytics_db.close_connection()


@pytest.mark.integration
def test_weekly_retention_counts_returning_user(tmp_path, monkeypatch):
    analytics_db.close_connection()
    monkeypatch.setattr(analytics_db, "_DB_PATH", tmp_path / "analytics.db")
    try:
        analytics_db.init_db()
        analytics_db.track_user(1)
        analytics_db.track_event(1, "download")
        registered = datetime.now(UTC) - timedelta(days=15)
        returned = registered + timedelta(days=8)
        with analytics_db._cursor_write() as cur:
            cur.execute("UPDATE users SET first_seen = ? WHERE user_id = 1", (registered.isoformat(),))
            cur.execute("UPDATE events SET ts = ? WHERE user_id = 1", (returned.isoformat(),))

        cohorts = analytics_db.cohort_retention(weeks=8)

        assert len(cohorts) == 1
        assert cohorts[0]["w1"] == 100.0
        assert cohorts[0]["w2"] == 0.0
    finally:
        analytics_db.close_connection()
