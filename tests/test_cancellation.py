"""Тесты отмены длительных задач.

Кнопка отмены обязана именно прерывать работу, а не прятать её результат:
иначе сервер всё равно качает файл, который пользователь уже не ждёт. yt-dlp
даёт для этого единственную точку — progress hook, поэтому отмена доходит до
загрузки через него.
"""

import pytest

from utils.cancellation import (
    CancelledByUser,
    cancellation_hook,
    forget_cancellation,
    is_cancelled,
    request_cancellation,
)


SESSION = "42_abcdef"
OTHER = "42_fedcba"

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def clean_registry():
    yield
    forget_cancellation(SESSION)
    forget_cancellation(OTHER)


def test_unknown_session_is_not_cancelled():
    assert is_cancelled(SESSION) is False


def test_requested_cancellation_is_visible():
    request_cancellation(SESSION)

    assert is_cancelled(SESSION) is True


def test_cancellation_touches_only_its_own_session():
    request_cancellation(SESSION)

    assert is_cancelled(OTHER) is False


def test_forgetting_releases_the_session():
    """Идентификаторы сессий не переиспользуются, но реестр не должен расти."""
    request_cancellation(SESSION)
    forget_cancellation(SESSION)

    assert is_cancelled(SESSION) is False


def test_hook_lets_a_live_download_continue():
    hook = cancellation_hook(SESSION)

    hook({"status": "downloading", "downloaded_bytes": 1024})


def test_hook_stops_a_cancelled_download():
    hook = cancellation_hook(SESSION)
    request_cancellation(SESSION)

    with pytest.raises(CancelledByUser):
        hook({"status": "downloading", "downloaded_bytes": 1024})


def test_hook_stops_at_the_very_first_callback():
    """Отмена до старта не должна ждать первого скачанного байта."""
    request_cancellation(SESSION)
    hook = cancellation_hook(SESSION)

    with pytest.raises(CancelledByUser):
        hook({"status": "started"})


def test_old_marks_are_forgotten_by_themselves(monkeypatch):
    """Отмену запоминает реестр в процессе — он не должен расти вечно.

    Очистка сессии стирать отметку не может: она происходит сразу по нажатию
    кнопки, а загрузчик читает признак позже — так отмена гасла бы, не сработав.
    """
    from utils import cancellation

    clock = [1000.0]
    monkeypatch.setattr(cancellation.time, "monotonic", lambda: clock[0])

    request_cancellation(SESSION)
    clock[0] += cancellation.CANCELLATION_MEMORY_SECONDS + 1

    assert is_cancelled(SESSION) is False
