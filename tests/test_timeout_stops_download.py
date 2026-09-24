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
import ast
import inspect
import textwrap

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


def test_session_callbacks_track_every_blocking_operation():
    """Все фоновые операции кнопок входят в учет отмены и отложенной очистки."""
    for handler in (
        telegram_utils._handle_main_callback,
        telegram_utils._download_and_send_subtitles,
        telegram_utils._send_photo_post_assets,
    ):
        tree = ast.parse(textwrap.dedent(inspect.getsource(handler)))
        calls = (
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "run_blocking"
        )
        for call in calls:
            assert any(keyword.arg == "session_id" for keyword in call.keywords), (
                f"{handler.__name__}: вызов run_blocking без session_id"
            )


def test_cleanup_waits_for_timed_out_worker(monkeypatch):
    import threading
    import time

    finished = threading.Event()
    cleaned = threading.Event()
    monkeypatch.setattr(
        telegram_utils,
        "cleanup_temp_files",
        lambda session_id: cleaned.set(),
    )

    def slow():
        time.sleep(0.2)
        finished.set()

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(telegram_utils.run_blocking(slow, session_id=SESSION))
    telegram_utils._cleanup_session_when_idle(SESSION)

    assert not cleaned.is_set()
    assert finished.wait(1)
    assert cleaned.wait(1)
