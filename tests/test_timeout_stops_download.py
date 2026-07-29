"""Таймаут блокирующей задачи обязан останавливать саму задачу.

Из прода, сессия 5f0bdac6: таймаут сработал в 02:06:36, а в 02:10:50 — через
4 минуты 14 секунд после него — в логе появилось «Видео успешно скачано».
`asyncio.wait_for` отменяет только ожидание: поток в пуле продолжает качать,
занимает воркер из восьми и тянет трафик ради файла, который уже никто не
получит.

Точка остановки у yt-dlp одна — progress hook, и он уже читает реестр отмен.
Значит таймауту достаточно попросить отмену той же сессии.
"""

import asyncio
import inspect

import pytest

from utils import telegram_utils
from utils.cancellation import forget_cancellation, is_cancelled


SESSION = "7_timeout-session"

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def clean_registry(monkeypatch):
    monkeypatch.setattr(telegram_utils, "BLOCKING_TASK_TIMEOUT", 0.05)
    yield
    forget_cancellation(SESSION)


def _slow():
    """Изображает yt-dlp, который качает дольше таймаута."""
    import time

    time.sleep(0.5)


def test_timeout_asks_the_session_to_stop():
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(
            telegram_utils.run_blocking(_slow, description="тест", session_id=SESSION)
        )

    assert is_cancelled(SESSION) is True


def test_timeout_without_a_session_changes_nothing():
    """Не все блокирующие вызовы принадлежат сессии — их поведение прежнее."""
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(telegram_utils.run_blocking(_slow, description="тест"))

    assert is_cancelled(SESSION) is False


def test_successful_task_is_not_cancelled():
    assert asyncio.run(
        telegram_utils.run_blocking(lambda: "готово", description="тест", session_id=SESSION)
    ) == "готово"
    assert is_cancelled(SESSION) is False


def test_downloads_pass_their_session_to_the_timeout():
    """Иначе правка бесполезна: таймауту нечего будет отменять."""
    source = inspect.getsource(telegram_utils.download_content)

    assert source.count("session_id=session_id") == source.count("await run_blocking(")
