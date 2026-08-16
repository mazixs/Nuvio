#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Интеграционные тесты для CSI (Customer Satisfaction Index) метрик.
"""

import warnings
from datetime import UTC, datetime, timedelta

import pytest

from utils import analytics_db


@pytest.fixture
def fresh_analytics_db(tmp_path, monkeypatch):
    """Создаёт изолированную аналитическую БД во временной директории."""
    db_path = tmp_path / "analytics_test.db"
    monkeypatch.setattr(analytics_db, "_DB_PATH", db_path)

    # Сбрасываем thread-local соединение, чтобы пересоздалось с новым путём
    if hasattr(analytics_db._local, "conn"):
        analytics_db._local.conn.close()
        delattr(analytics_db._local, "conn")

    analytics_db.init_db()
    yield analytics_db

    # Очистка
    if hasattr(analytics_db._local, "conn"):
        analytics_db._local.conn.close()
        delattr(analytics_db._local, "conn")


@pytest.mark.integration
def test_init_db_creates_csi_schema(fresh_analytics_db):
    """Проверяет что init_db создаёт таблицу csi_responses и колонку last_csi_sent."""
    db = fresh_analytics_db
    with db._cursor_read() as cur:
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='csi_responses'"
        )
        assert cur.fetchone() is not None

        cur.execute("PRAGMA table_info(users)")
        columns = {row[1] for row in cur.fetchall()}
        assert "last_csi_sent" in columns


@pytest.mark.integration
def test_analytics_timestamps_use_a_non_deprecated_utc_clock(fresh_analytics_db):
    """Запись аналитики не должна обращаться к удалённому utcnow()."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        fresh_analytics_db.track_user(1, "user1")
        fresh_analytics_db.track_event(1, "start")
        fresh_analytics_db.update_last_csi_sent(1)
        fresh_analytics_db.save_csi_rating(1, 5)


@pytest.mark.integration
def test_close_connection_releases_the_thread_local_connection(fresh_analytics_db):
    """Жизненный цикл приложения может закрыть соединение своего потока."""
    assert hasattr(fresh_analytics_db._local, "conn")

    fresh_analytics_db.close_connection()

    assert not hasattr(fresh_analytics_db._local, "conn")


@pytest.mark.integration
def test_get_users_for_csi(fresh_analytics_db):
    """Проверяет выборку пользователей для CSI-опроса."""
    db = fresh_analytics_db
    old = (datetime.now(UTC) - timedelta(days=10)).isoformat()

    db.track_user(1, "user1")
    db.track_user(2, "user2")
    db.track_user(3, "user3")

    # user1: нет last_csi_sent, активен (по умолчанию last_seen = now)
    # user2: старый last_csi_sent, активен
    # user3: неактивен (last_seen давно)
    with db._cursor_write() as cur:
        cur.execute("UPDATE users SET last_csi_sent = ? WHERE user_id = ?", (old, 2))
        cur.execute("UPDATE users SET last_seen = ? WHERE user_id = ?", (old, 3))

    users = db.get_users_for_csi(days_since_last=7, min_active_days=1)
    assert 1 in users
    assert 2 in users
    assert 3 not in users


@pytest.mark.integration
def test_update_last_csi_sent(fresh_analytics_db):
    """Проверяет обновление времени последней отправки CSI."""
    db = fresh_analytics_db
    db.track_user(1, "user1")

    db.update_last_csi_sent(1)

    with db._cursor_read() as cur:
        cur.execute("SELECT last_csi_sent FROM users WHERE user_id = ?", (1,))
        row = cur.fetchone()
        assert row["last_csi_sent"] is not None


@pytest.mark.integration
def test_save_csi_rating(fresh_analytics_db):
    """Проверяет сохранение оценки CSI."""
    db = fresh_analytics_db
    csi_id = db.save_csi_rating(1, 8)
    assert isinstance(csi_id, int)
    assert csi_id > 0

    with db._cursor_read() as cur:
        cur.execute("SELECT * FROM csi_responses WHERE id = ?", (csi_id,))
        row = cur.fetchone()
        assert row["user_id"] == 1
        assert row["rating"] == 8
        assert row["feedback"] is None


@pytest.mark.integration
def test_update_csi_feedback(fresh_analytics_db):
    """Проверяет обновление отзыва к существующей записи CSI."""
    db = fresh_analytics_db
    csi_id = db.save_csi_rating(1, 5)
    db.update_csi_feedback(csi_id, "Медленно загружает видео")

    with db._cursor_read() as cur:
        cur.execute("SELECT feedback FROM csi_responses WHERE id = ?", (csi_id,))
        assert cur.fetchone()["feedback"] == "Медленно загружает видео"


@pytest.mark.integration
def test_get_csi_metrics(fresh_analytics_db):
    """Проверяет агрегированные метрики CSI."""
    db = fresh_analytics_db

    # Высокая оценка без отзыва
    db.save_csi_rating(1, 9)
    # Низкая оценка с отзывом
    csi_id = db.save_csi_rating(2, 4)
    db.update_csi_feedback(csi_id, "Не работает TikTok")

    metrics = db.get_csi_metrics()
    assert metrics["total_responses"] == 2
    assert metrics["avg_rating"] == 6.5
    assert metrics["distribution"][9] == 1
    assert metrics["distribution"][4] == 1
    assert len(metrics["recent_low_feedback"]) == 1
    assert metrics["recent_low_feedback"][0]["feedback"] == "Не работает TikTok"


@pytest.mark.integration
def test_get_csi_metrics_empty_db(fresh_analytics_db):
    """Проверяет метрики при пустой таблице CSI."""
    metrics = fresh_analytics_db.get_csi_metrics()
    assert metrics == {
        "avg_rating": 0.0,
        "total_responses": 0,
        "distribution": {},
        "recent_low_feedback": [],
    }
