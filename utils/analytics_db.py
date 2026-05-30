"""
Аналитическая SQLite база данных для трекинга пользователей и событий.

Оптимизации WAL:
- timeout=30.0: терпеливое ожидание при блокировке
- synchronous=NORMAL: ускорение записи без потери целостности
- cache_size=-64000: 64 МБ кэш страниц
- isolation_level=None + BEGIN IMMEDIATE: ручное управление транзакциями
  и защита от upgrade deadlock при конкурентной записи.
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
    """Возвращает thread-local соединение с настроенными PRAGMA."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(
            str(_DB_PATH),
            check_same_thread=False,
            timeout=30.0,
            isolation_level=None,
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-64000")
        conn.row_factory = sqlite3.Row
        _local.conn = conn
    return conn


@contextmanager
def _cursor_read():
    """Контекстный менеджер для операций чтения."""
    conn = _get_connection()
    cur = conn.cursor()
    try:
        yield cur
    finally:
        cur.close()


@contextmanager
def _cursor_write():
    """
    Контекстный менеджер для операций записи.
    
    Использует BEGIN IMMEDIATE для резервирования права записи
    на старте транзакции, предотвращая upgrade deadlock.
    """
    conn = _get_connection()
    cur = conn.cursor()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield cur
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        cur.close()


def init_db() -> None:
    """Создаёт таблицы если не существуют."""
    with _cursor_write() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id       INTEGER PRIMARY KEY,
                username      TEXT,
                first_name    TEXT,
                last_name     TEXT,
                language_code TEXT,
                first_seen    TEXT NOT NULL,
                last_seen     TEXT NOT NULL
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id   INTEGER NOT NULL,
                event     TEXT NOT NULL,
                platform  TEXT,
                url       TEXT,
                metadata  TEXT,
                ts        TEXT NOT NULL
            )
        """)

        # Таблица ответов CSI (Customer Satisfaction Index)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS csi_responses (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                rating      INTEGER NOT NULL,
                feedback    TEXT,
                created_at  TEXT NOT NULL
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_csi_user ON csi_responses(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_csi_created ON csi_responses(created_at)")

        # Миграция: добавляем last_csi_sent в users если колонка отсутствует
        cur.execute("PRAGMA table_info(users)")
        existing_cols = {row[1] for row in cur.fetchall()}
        if "last_csi_sent" not in existing_cols:
            cur.execute("ALTER TABLE users ADD COLUMN last_csi_sent TEXT")

        cur.execute("CREATE INDEX IF NOT EXISTS idx_events_user   ON events(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_events_ts     ON events(ts)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_events_event  ON events(event)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_events_platform ON events(platform)")


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
    with _cursor_write() as cur:
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
    with _cursor_write() as cur:
        cur.execute(
            "INSERT INTO events (user_id, event, platform, url, metadata, ts) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, event, platform, url, metadata, now),
        )


# ── CSI (Customer Satisfaction Index) ───────────────────────────


def get_users_for_csi(days_since_last: int = 7, min_active_days: int = 1) -> list[int]:
    """
    Возвращает ID пользователей, которым пора отправить CSI-опрос.

    Условия:
    - Пользователь активен за последние min_active_days дней
    - CSI не отправлялся ранее ИЛИ прошло более days_since_last дней
    """
    active_since = (datetime.utcnow() - timedelta(days=min_active_days)).isoformat()
    last_csi_since = (datetime.utcnow() - timedelta(days=days_since_last)).isoformat()
    with _cursor_read() as cur:
        cur.execute(
            """
            SELECT user_id FROM users
            WHERE last_seen >= ?
              AND (last_csi_sent IS NULL OR last_csi_sent < ?)
            ORDER BY last_seen DESC
            """,
            (active_since, last_csi_since),
        )
        return [int(row["user_id"]) for row in cur.fetchall()]


def update_last_csi_sent(user_id: int) -> None:
    """Обновляет время последней отправки CSI для пользователя."""
    now = datetime.utcnow().isoformat()
    with _cursor_write() as cur:
        cur.execute(
            "UPDATE users SET last_csi_sent = ? WHERE user_id = ?",
            (now, user_id),
        )


def save_csi_rating(user_id: int, rating: int) -> int:
    """
    Сохраняет оценку CSI.

    Returns:
        ID созданной записи
    """
    now = datetime.utcnow().isoformat()
    with _cursor_write() as cur:
        cur.execute(
            "INSERT INTO csi_responses (user_id, rating, created_at) VALUES (?, ?, ?)",
            (user_id, rating, now),
        )
        return cur.lastrowid


def update_csi_feedback(csi_id: int, feedback: str) -> None:
    """Добавляет текстовый отзыв к существующей записи CSI."""
    with _cursor_write() as cur:
        cur.execute(
            "UPDATE csi_responses SET feedback = ? WHERE id = ?",
            (feedback, csi_id),
        )


def get_csi_metrics() -> dict:
    """
    Возвращает метрики CSI.

    Returns:
        dict с avg_rating, total_responses, distribution, recent_low_feedback
    """
    with _cursor_read() as cur:
        cur.execute("SELECT COUNT(*) FROM csi_responses")
        total = cur.fetchone()[0]

        if total == 0:
            return {
                "avg_rating": 0.0,
                "total_responses": 0,
                "distribution": {},
                "recent_low_feedback": [],
            }

        cur.execute("SELECT AVG(rating) FROM csi_responses")
        avg = round(cur.fetchone()[0] or 0, 1)

        cur.execute(
            """
            SELECT rating, COUNT(*) as cnt
            FROM csi_responses
            GROUP BY rating
            ORDER BY rating
            """
        )
        distribution = {int(row["rating"]): row["cnt"] for row in cur.fetchall()}

        cur.execute(
            """
            SELECT id, user_id, rating, feedback, created_at
            FROM csi_responses
            WHERE rating < 7 AND feedback IS NOT NULL AND feedback != ''
            ORDER BY created_at DESC
            LIMIT 10
            """
        )
        recent_low = [dict(row) for row in cur.fetchall()]

        return {
            "avg_rating": avg,
            "total_responses": total,
            "distribution": distribution,
            "recent_low_feedback": recent_low,
        }


# ── метрики ─────────────────────────────────────────────────────


def total_users() -> int:
    with _cursor_read() as cur:
        cur.execute("SELECT COUNT(*) FROM users")
        return cur.fetchone()[0]


def new_users(days: int = 1) -> int:
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with _cursor_read() as cur:
        cur.execute("SELECT COUNT(*) FROM users WHERE first_seen >= ?", (since,))
        return cur.fetchone()[0]


def active_users(days: int = 1) -> int:
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with _cursor_read() as cur:
        cur.execute("SELECT COUNT(DISTINCT user_id) FROM events WHERE ts >= ?", (since,))
        return cur.fetchone()[0]


def retention(day: int) -> float:
    """Retention на N-й день: % пользователей, вернувшихся ровно через day дней после first_seen."""
    with _cursor_read() as cur:
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
    with _cursor_read() as cur:
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
    with _cursor_read() as cur:
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
    with _cursor_read() as cur:
        cur.execute("SELECT COUNT(*) FROM events WHERE event = 'download'")
        return cur.fetchone()[0]


def popular_videos(limit: int = 20) -> list[dict]:
    with _cursor_read() as cur:
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
    with _cursor_read() as cur:
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
    with _cursor_read() as cur:
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
    with _cursor_read() as cur:
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


def get_all_user_ids() -> list[int]:
    """Возвращает ID всех известных пользователей для админской рассылки."""
    with _cursor_read() as cur:
        cur.execute("SELECT user_id FROM users ORDER BY last_seen DESC")
        return [int(row["user_id"]) for row in cur.fetchall()]


def get_user_detail(user_id: int) -> dict | None:
    with _cursor_read() as cur:
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
    with _cursor_read() as cur:
        cur.execute("SELECT COUNT(*) FROM users")
        total = cur.fetchone()[0]
        if total == 0:
            return 0.0
        cur.execute("SELECT COUNT(*) FROM events WHERE event = 'download'")
        dl = cur.fetchone()[0]
        return round(dl / total, 1)


def repeat_users_rate() -> float:
    """% пользователей с более чем 1 скачиванием."""
    with _cursor_read() as cur:
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
    with _cursor_read() as cur:
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
    with _cursor_read() as cur:
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
    with _cursor_read() as cur:
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
        # CSI метрики
        "csi": get_csi_metrics(),
    }
