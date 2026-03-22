"""
Кэширование file_id для мгновенной доставки видео.

Telegram сохраняет загруженные файлы и присваивает им file_id.
При повторной отправке того же file_id - доставка мгновенна (0 секунд).
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from contextlib import contextmanager
from config import BASE_DIR
from utils.logger import setup_logger

logger = setup_logger(__name__)

@dataclass
class CachedVideo:
    """Закэшированное видео в Telegram."""
    url: str
    file_id: str
    file_unique_id: str
    platform: str
    format_id: str
    cached_at: datetime
    file_size: int | None = None
    duration: int | None = None
    title: str | None = None
    
    def is_valid(self, cache_ttl_days: int = 90) -> bool:
        """
        Проверяет, не истек ли кэш.
        
        Args:
            cache_ttl_days: TTL кэша в днях (по умолчанию 90 дней)
            
        Returns:
            True если кэш валиден
        """
        age = datetime.now() - self.cached_at
        return age < timedelta(days=cache_ttl_days)
    
    def to_dict(self) -> dict:
        """Конвертирует в словарь для сериализации."""
        return {
            'url': self.url,
            'file_id': self.file_id,
            'file_unique_id': self.file_unique_id,
            'platform': self.platform,
            'format_id': self.format_id,
            'cached_at': self.cached_at.isoformat(),
            'file_size': self.file_size,
            'duration': self.duration,
            'title': self.title
        }


class TelegramVideoCache:
    """
    Кэш file_id для видео.
    
    Обеспечивает мгновенную доставку для повторных запросов:
    - 1-й запрос: скачивание + загрузка (5-10 мин)
    - 2+ запросы: мгновенная отправка (0 сек)
    
    Использует SQLite для персистентности.
    """
    
    def __init__(self, db_path: Path | None = None):
        """
        Инициализирует кэш.
        
        Args:
            db_path: Путь к файлу базы данных. 
                     По умолчанию BASE_DIR / "telegram_cache.db"
        """
        if db_path is None:
            import os
            data_dir = Path(os.environ.get("DATA_DIR", str(BASE_DIR)))
            db_path = data_dir / "telegram_cache.db"
        
        self.db_path = db_path
        self._init_db()
        logger.info(f"Telegram video cache инициализирован: {self.db_path}")
    
    @contextmanager
    def _get_connection(self):
        """Context manager для безопасной работы с БД."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            yield conn
        finally:
            conn.close()
    
    def _init_db(self):
        """Инициализирует схему базы данных."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS video_cache (
                    url TEXT NOT NULL,
                    file_id TEXT NOT NULL,
                    file_unique_id TEXT NOT NULL UNIQUE,
                    platform TEXT NOT NULL,
                    format_id TEXT NOT NULL,
                    cached_at TEXT NOT NULL,
                    file_size INTEGER,
                    duration INTEGER,
                    title TEXT,
                    PRIMARY KEY (url, format_id)
                )
            """)
            
            # Индексы для быстрого поиска
            conn.execute("CREATE INDEX IF NOT EXISTS idx_url ON video_cache(url)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_file_id ON video_cache(file_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_platform ON video_cache(platform)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cached_at ON video_cache(cached_at)")
            
            conn.commit()
    
    def get(
        self, 
        url: str, 
        format_id: str = "best",
        check_validity: bool = True
    ) -> CachedVideo | None:
        """
        Получает file_id из кэша.
        
        Args:
            url: URL видео
            format_id: ID формата (best, 720p, audio и т.д.)
            check_validity: Проверять ли TTL кэша
            
        Returns:
            CachedVideo если найден в кэше и валиден, иначе None
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM video_cache WHERE url = ? AND format_id = ?",
                (url, format_id)
            )
            row = cursor.fetchone()
        
        if not row:
            logger.debug(f"Cache MISS для {url} (format: {format_id})")
            return None
        
        cached = CachedVideo(
            url=row[0],
            file_id=row[1],
            file_unique_id=row[2],
            platform=row[3],
            format_id=row[4],
            cached_at=datetime.fromisoformat(row[5]),
            file_size=row[6],
            duration=row[7],
            title=row[8]
        )
        
        # Проверяем валидность
        if check_validity and not cached.is_valid():
            logger.warning(f"Кэш устарел для {url}, удаляем")
            self.delete(url, format_id)
            return None
        
        logger.info(f"✅ Cache HIT для {url} (format: {format_id}, age: {(datetime.now() - cached.cached_at).days} дней)")
        return cached
    
    def set(self, cached: CachedVideo):
        """
        Сохраняет file_id в кэш.
        
        Args:
            cached: Объект CachedVideo для сохранения
        """
        with self._get_connection() as conn:
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO video_cache 
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    cached.url,
                    cached.file_id,
                    cached.file_unique_id,
                    cached.platform,
                    cached.format_id,
                    cached.cached_at.isoformat(),
                    cached.file_size,
                    cached.duration,
                    cached.title
                ))
                conn.commit()
                logger.info(f"💾 Сохранен в кэш: {cached.url} -> {cached.file_id}")
            except sqlite3.IntegrityError as e:
                logger.error(f"Ошибка сохранения в кэш: {e}")
    
    def delete(self, url: str, format_id: str):
        """
        Удаляет запись из кэша.
        
        Args:
            url: URL видео
            format_id: ID формата
        """
        with self._get_connection() as conn:
            conn.execute(
                "DELETE FROM video_cache WHERE url = ? AND format_id = ?",
                (url, format_id)
            )
            conn.commit()
            logger.debug(f"Удалено из кэша: {url} (format: {format_id})")
    
    def delete_by_file_id(self, file_id: str):
        """
        Удаляет запись по file_id (если Telegram вернул ошибку).
        
        Args:
            file_id: file_id для удаления
        """
        with self._get_connection() as conn:
            conn.execute("DELETE FROM video_cache WHERE file_id = ?", (file_id,))
            conn.commit()
            logger.debug(f"Удалено из кэша по file_id: {file_id}")
    
    def cleanup_expired(self, ttl_days: int = 90):
        """
        Удаляет устаревшие записи.
        
        Args:
            ttl_days: TTL кэша в днях
        """
        cutoff = (datetime.now() - timedelta(days=ttl_days)).isoformat()
        
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM video_cache WHERE cached_at < ?",
                (cutoff,)
            )
            deleted = cursor.rowcount
            conn.commit()
        
        if deleted > 0:
            logger.info(f"🗑️ Очищено {deleted} устаревших записей из кэша")
        
        return deleted
    
    def vacuum(self) -> None:
        """Выполняет VACUUM и checkpoint для базы кэша."""
        with self._get_connection() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("VACUUM")
            conn.commit()
        logger.info("🧹 Выполнена оптимизация базы кэша (VACUUM)")
    
    def get_stats(self) -> dict:
        """
        Получает статистику по кэшу.
        
        Returns:
            Словарь со статистикой
        """
        with self._get_connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM video_cache").fetchone()[0]
            
            by_platform = dict(conn.execute("""
                SELECT platform, COUNT(*) 
                FROM video_cache 
                GROUP BY platform
            """).fetchall())
            
            oldest = conn.execute(
                "SELECT MIN(cached_at) FROM video_cache"
            ).fetchone()[0]
            
            newest = conn.execute(
                "SELECT MAX(cached_at) FROM video_cache"
            ).fetchone()[0]
        
        return {
            'total_videos': total,
            'by_platform': by_platform,
            'oldest_entry': oldest,
            'newest_entry': newest
        }
    
    def search_by_title(self, query: str, limit: int = 10) -> list[CachedVideo]:
        """
        Ищет видео по названию.
        
        Args:
            query: Поисковый запрос
            limit: Максимум результатов
            
        Returns:
            Список CachedVideo
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """SELECT * FROM video_cache 
                   WHERE title LIKE ? 
                   ORDER BY cached_at DESC 
                   LIMIT ?""",
                (f"%{query}%", limit)
            )
            rows = cursor.fetchall()
        
        return [
            CachedVideo(
                url=row[0],
                file_id=row[1],
                file_unique_id=row[2],
                platform=row[3],
                format_id=row[4],
                cached_at=datetime.fromisoformat(row[5]),
                file_size=row[6],
                duration=row[7],
                title=row[8]
            )
            for row in rows
        ]


# Глобальный экземпляр кэша
telegram_cache = TelegramVideoCache()
