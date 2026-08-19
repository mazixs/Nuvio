"""Побочный канал диагностики загрузки: строки yt-dlp и фактический формат.

Нужен для двух вещей, и обе всплыли на поломке YouTube 18.08.2026.

Первая — краш-репорт. Объяснения отказа живут в предупреждениях yt-dlp («cookies
are no longer valid», «formats require a GVS PO Token», «forcing SABR
streaming»), а в отчёт админам они не попадали вовсе, и их приходилось добывать
по SSH. Здесь копятся последние строки вывода, чтобы отчёт нёс их с собой.

Вторая — кэш `file_id`. Каскад фолбеков при 403 молча подменяет формат: запрос
`18` уезжает в `bestvideo+bestaudio`, и файл возвращается как запрошенный.
Кэш пишется по паре «url + запрошенный format_id», поэтому кнопка «1080p» могла
навсегда отдавать 240p. Загрузчик записывает сюда формат, который реально
принёс, а хэндлер решает, под каким ключом кэшировать и стоит ли вообще.

Канал именно побочный: загрузчики возвращают путь к файлу, и менять их сигнатуры
ради диагностики значило бы тронуть все пять каскадов в `youtube_utils.py`.

Состояние делят event loop и потоки пула загрузок, поэтому доступ под замком.
"""

from __future__ import annotations

import threading
from collections import OrderedDict, deque

__all__ = [
    "OUTPUT_TAIL",
    "TRACKED_SESSIONS",
    "delivered_format",
    "forget",
    "output_tail",
    "record_delivered_format",
    "record_output",
]

# Сколько последних строк вывода yt-dlp хранить на сессию. Сорванная загрузка
# успевает сделать до восемнадцати попыток (3 backoff × 2 режима cookies +
# 6 вариантов CLI), и каждая пишет свои предупреждения — хвоста хватает, чтобы
# увидеть последнюю попытку целиком, а не разрастись на всю сессию.
OUTPUT_TAIL = 60

# Сколько сессий держать. Сессий у пользователя максимум 5, пользователей
# немного, но регистр не должен расти без предела, если `forget` где-то не
# позовут: самая старая запись вытесняется.
TRACKED_SESSIONS = 64

_LOCK = threading.Lock()
_OUTPUT: OrderedDict[str, deque[str]] = OrderedDict()
_DELIVERED: OrderedDict[str, str] = OrderedDict()

# Ключ для вызовов вне сессии: разбор ссылки идёт до её появления, а его
# предупреждения тоже объясняют отказ.
_NO_SESSION = "-"


def _key(session_id: str | None) -> str:
    return session_id or _NO_SESSION


def _evict(store: OrderedDict) -> None:
    while len(store) > TRACKED_SESSIONS:
        store.popitem(last=False)


def record_output(session_id: str | None, line: str) -> None:
    """Складывает строку вывода yt-dlp в хвост сессии."""
    text = line.strip()
    if not text:
        return
    key = _key(session_id)
    with _LOCK:
        lines = _OUTPUT.get(key)
        if lines is None:
            lines = deque(maxlen=OUTPUT_TAIL)
            _OUTPUT[key] = lines
        _OUTPUT.move_to_end(key)
        lines.append(text)
        _evict(_OUTPUT)


def output_tail(session_id: str | None) -> list[str]:
    """Возвращает последние строки вывода yt-dlp по сессии.

    Строки вне сессии добавляются в начало: разбор ссылки идёт раньше загрузки,
    и его предупреждения объясняют, каким клиентом получен список форматов.
    """
    with _LOCK:
        shared = list(_OUTPUT.get(_NO_SESSION, ()))
        if session_id is None:
            return shared
        return shared + list(_OUTPUT.get(_key(session_id), ()))


def record_delivered_format(session_id: str | None, format_id: str | None) -> None:
    """Запоминает формат, который загрузчик реально принёс."""
    if not format_id:
        return
    key = _key(session_id)
    with _LOCK:
        _DELIVERED[key] = str(format_id)
        _DELIVERED.move_to_end(key)
        _evict(_DELIVERED)


def delivered_format(session_id: str | None) -> str | None:
    """Возвращает фактически скачанный формат либо None, если он неизвестен."""
    with _LOCK:
        return _DELIVERED.get(_key(session_id))


def forget(session_id: str | None) -> None:
    """Убирает записи сессии. Зовётся при её уничтожении."""
    key = _key(session_id)
    with _LOCK:
        _OUTPUT.pop(key, None)
        _DELIVERED.pop(key, None)
