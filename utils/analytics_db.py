"""
Аналитическая SQLite база данных для трекинга пользователей и событий.
"""
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import os

_DATA_DIR = Path(os.environ.get("DATA_DIR", str(Path(__file__).resolve().parent.parent)))
_DB_PATH = Path(_DATA_DIR) / "analytics.db"
_local = threading.local()


def _get_connection() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        _local.conn = conn
    return conn


@contextmanager
def _cursor():
    conn = _get_connection()
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def init_db() -> None:
    """Создаёт таблицы если не существуют."""
    with _cursor() as cur:
        cur.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id       INTEGER PRIMARY KEY,
                username      TEXT,
                first_name    TEXT,
                last_name     TEXT,
                language_code TEXT,
                first_seen    TEXT NOT NULL,
                last_seen     TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS events (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id   INTEGER NOT NULL,
                event     TEXT NOT NULL,
                platform  TEXT,
                url       TEXT,
                metadata  TEXT,
                ts        TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_events_user   ON events(user_id);
            CREATE INDEX IF NOT EXISTS idx_events_ts     ON events(ts);
            CREATE INDEX IF NOT EXISTS idx_events_event  ON events(event);
            CREATE INDEX IF NOT EXISTS idx_events_platform ON events(platform);
        """)


# ── запись событий ──────────────────────────────────────────────


def track_user(
    user_id: int,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    language_code: str | None = None,
) -> None:
    """Создаёт или обновляет пользователя."""
    now = datetime.utcnow().isoformat()
    with _cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (user_id, username, first_name, last_name, language_code, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username      = COALESCE(excluded.username, users.username),
                first_name    = COALESCE(excluded.first_name, users.first_name),
                last_name     = COALESCE(excluded.last_name, users.last_name),
                language_code = COALESCE(excluded.language_code, users.language_code),
                last_seen     = excluded.last_seen
            """,
            (user_id, username, first_name, last_name, language_code, now, now),
        )


def track_event(
    user_id: int,
    event: str,
    platform: str | None = None,
    url: str | None = None,
    metadata: str | None = None,
) -> None:
    """Записывает событие."""
    now = datetime.utcnow().isoformat()
    with _cursor() as cur:
        cur.execute(
            "INSERT INTO events (user_id, event, platform, url, metadata, ts) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, event, platform, url, metadata, now),
        )


# ── метрики ─────────────────────────────────────────────────────


def total_users() -> int:
    with _cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM users")
        return cur.fetchone()[0]


def new_users(days: int = 1) -> int:
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with _cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM users WHERE first_seen >= ?", (since,))
        return cur.fetchone()[0]


def active_users(days: int = 1) -> int:
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with _cursor() as cur:
        cur.execute("SELECT COUNT(DISTINCT user_id) FROM events WHERE ts >= ?", (since,))
        return cur.fetchone()[0]


def retention(day: int) -> float:
    """Retention на N-й день: % пользователей, вернувшихся ровно через day дней после first_seen."""
    with _cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM users")
        total = cur.fetchone()[0]
        if total == 0:
            return 0.0
        cur.execute(
            """
            SELECT COUNT(DISTINCT u.user_id)
            FROM users u
            JOIN events e ON e.user_id = u.user_id
            WHERE DATE(e.ts) = DATE(u.first_seen, '+' || ? || ' days')
              AND DATE(u.first_seen) <= DATE('now', '-' || ? || ' days')
            """,
            (day, day),
        )
        retained = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM users WHERE DATE(first_seen) <= DATE('now', '-' || ? || ' days')",
            (day,),
        )
        eligible = cur.fetchone()[0]
        if eligible == 0:
            return 0.0
        return round(retained / eligible * 100, 1)


def churn_rate(days: int = 30) -> float:
    """Churn: % пользователей, которые были активны ранее, но не активны за последние days дней."""
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with _cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM users")
        total = cur.fetchone()[0]
        if total == 0:
            return 0.0
        cur.execute(
            "SELECT COUNT(*) FROM users WHERE last_seen < ?",
            (since,),
        )
        churned = cur.fetchone()[0]
        return round(churned / total * 100, 1)


def downloads_by_platform() -> dict[str, int]:
    with _cursor() as cur:
        cur.execute(
            """
            SELECT COALESCE(platform, 'unknown') as p, COUNT(*) as c
            FROM events
            WHERE event = 'download'
            GROUP BY p
            ORDER BY c DESC
            """,
        )
        return {row["p"]: row["c"] for row in cur.fetchall()}


def total_downloads() -> int:
    with _cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM events WHERE event = 'download'")
        return cur.fetchone()[0]


def popular_videos(limit: int = 20) -> list[dict]:
    with _cursor() as cur:
        cur.execute(
            """
            SELECT url, platform, COUNT(*) as cnt, MAX(ts) as last_download
            FROM events
            WHERE event = 'download' AND url IS NOT NULL
            GROUP BY url
            ORDER BY cnt DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]


def downloads_per_day(days: int = 30) -> list[dict]:
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with _cursor() as cur:
        cur.execute(
            """
            SELECT DATE(ts) as day, COUNT(*) as cnt
            FROM events
            WHERE event = 'download' AND ts >= ?
            GROUP BY day
            ORDER BY day
            """,
            (since,),
        )
        return [dict(row) for row in cur.fetchall()]


def new_users_per_day(days: int = 30) -> list[dict]:
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with _cursor() as cur:
        cur.execute(
            """
            SELECT DATE(first_seen) as day, COUNT(*) as cnt
            FROM users
            WHERE first_seen >= ?
            GROUP BY day
            ORDER BY day
            """,
            (since,),
        )
        return [dict(row) for row in cur.fetchall()]


def get_all_users(limit: int = 100, offset: int = 0) -> list[dict]:
    with _cursor() as cur:
        cur.execute(
            """
            SELECT u.user_id, u.username, u.first_name, u.last_name,
                   u.language_code, u.first_seen, u.last_seen,
                   COUNT(e.id) as total_events,
                   SUM(CASE WHEN e.event = 'download' THEN 1 ELSE 0 END) as total_downloads
            FROM users u
            LEFT JOIN events e ON e.user_id = u.user_id
            GROUP BY u.user_id
            ORDER BY u.last_seen DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        return [dict(row) for row in cur.fetchall()]


def get_user_detail(user_id: int) -> dict | None:
    with _cursor() as cur:
        cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        if not row:
            return None
        user = dict(row)

        cur.execute(
            "SELECT COUNT(*) FROM events WHERE user_id = ? AND event = 'download'",
            (user_id,),
        )
        user["total_downloads"] = cur.fetchone()[0]

        cur.execute(
            """
            SELECT COALESCE(platform, 'unknown') as p, COUNT(*) as c
            FROM events
            WHERE user_id = ? AND event = 'download'
            GROUP BY p
            """,
            (user_id,),
        )
        user["downloads_by_platform"] = {row["p"]: row["c"] for row in cur.fetchall()}

        cur.execute(
            """
            SELECT event, platform, url, ts
            FROM events
            WHERE user_id = ?
            ORDER BY ts DESC
            LIMIT 50
            """,
            (user_id,),
        )
        user["recent_events"] = [dict(row) for row in cur.fetchall()]

        return user


def avg_downloads_per_user() -> float:
    """Среднее количество скачиваний на пользователя."""
    with _cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM users")
        total = cur.fetchone()[0]
        if total == 0:
            return 0.0
        cur.execute("SELECT COUNT(*) FROM events WHERE event = 'download'")
        dl = cur.fetchone()[0]
        return round(dl / total, 1)


def repeat_users_rate() -> float:
    """% пользователей с более чем 1 скачиванием."""
    with _cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM users")
        total = cur.fetchone()[0]
        if total == 0:
            return 0.0
        cur.execute("""
            SELECT COUNT(*) FROM (
                SELECT user_id FROM events WHERE event = 'download'
                GROUP BY user_id HAVING COUNT(*) > 1
            )
        """)
        repeat = cur.fetchone()[0]
        return round(repeat / total * 100, 1)


def engagement_score() -> float:
    """Индекс вовлечённости: DAU/MAU * 100 (stickiness ratio)."""
    dau = active_users(1)
    mau = active_users(30)
    if mau == 0:
        return 0.0
    return round(dau / mau * 100, 1)


def cohort_retention(weeks: int = 8) -> list[dict]:
    """Когортный анализ удержания по неделям регистрации.

    Возвращает список: [{week: "2026-W10", size: 15, w0: 100, w1: 60, w2: 40, ...}, ...]
    """
    with _cursor() as cur:
        # Получаем когорты (неделя регистрации)
        cur.execute(f"""
            SELECT strftime('%Y-W%W', first_seen) as cohort_week,
                   COUNT(*) as cohort_size
            FROM users
            WHERE first_seen >= DATE('now', '-{weeks * 7} days')
            GROUP BY cohort_week
            ORDER BY cohort_week
        """)
        cohorts = [dict(row) for row in cur.fetchall()]

        for cohort in cohorts:
            week = cohort["cohort_week"]
            size = cohort["cohort_size"]
            cohort["w0"] = 100.0  # неделя регистрации — всегда 100%

            for w in range(1, weeks + 1):
                cur.execute("""
                    SELECT COUNT(DISTINCT e.user_id)
                    FROM events e
                    JOIN users u ON u.user_id = e.user_id
                    WHERE strftime('%Y-W%%W', u.first_seen) = ?
                      AND CAST((julianday(e.ts) - julianday(u.first_seen)) / 7 AS INTEGER) = ?
                """, (week, w))
                returned = cur.fetchone()[0]
                cohort[f"w{w}"] = round(returned / size * 100, 1) if size > 0 else 0.0

        return cohorts


def engagement_per_day(days: int = 30) -> list[dict]:
    """DAU/MAU (stickiness) по дням за период.

    Возвращает: [{day: "2026-03-22", dau: 5, stickiness: 25.0}, ...]
    """
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with _cursor() as cur:
        # MAU для каждого дня = уникальные пользователи за 30 дней до этого дня
        cur.execute("""
            SELECT DATE(ts) as day, COUNT(DISTINCT user_id) as dau
            FROM events
            WHERE ts >= ?
            GROUP BY day
            ORDER BY day
        """, (since,))
        daily = [dict(row) for row in cur.fetchall()]

        # Общий MAU за весь период
        cur.execute("""
            SELECT COUNT(DISTINCT user_id) FROM events WHERE ts >= ?
        """, (since,))
        mau = cur.fetchone()[0] or 1

        for d in daily:
            d["stickiness"] = round(d["dau"] / mau * 100, 1)

        return daily


def platform_conversion() -> dict[str, dict]:
    """Конверсия по платформам: сколько уникальных пользователей скачивали с каждой."""
    with _cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM users")
        total = cur.fetchone()[0]
        if total == 0:
            return {}
        cur.execute("""
            SELECT COALESCE(platform, 'unknown') as p,
                   COUNT(*) as downloads,
                   COUNT(DISTINCT user_id) as users
            FROM events WHERE event = 'download'
            GROUP BY p ORDER BY downloads DESC
        """)
        result = {}
        for row in cur.fetchall():
            p = row["p"]
            result[p] = {
                "downloads": row["downloads"],
                "users": row["users"],
                "pct_users": round(row["users"] / total * 100, 1),
            }
        return result


def dashboard_summary() -> dict:
    """Полная сводка для дашборда."""
    return {
        "total_users": total_users(),
        "new_users_today": new_users(1),
        "new_users_7d": new_users(7),
        "new_users_30d": new_users(30),
        "active_today": active_users(1),
        "active_7d": active_users(7),
        "active_30d": active_users(30),
        "retention_3": retention(3),
        "retention_7": retention(7),
        "retention_30": retention(30),
        "churn_30": churn_rate(30),
        "total_downloads": total_downloads(),
        "downloads_by_platform": downloads_by_platform(),
        "downloads_per_day": downloads_per_day(30),
        "new_users_per_day": new_users_per_day(30),
        "popular_videos": popular_videos(10),
        # Продуктовые метрики
        "avg_downloads": avg_downloads_per_user(),
        "repeat_rate": repeat_users_rate(),
        "engagement": engagement_score(),
        "engagement_per_day": engagement_per_day(30),
        "cohorts": cohort_retention(8),
        "platform_conversion": platform_conversion(),
    }
