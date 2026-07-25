"""Тесты пути отмены: от нажатия кнопки до прерванной загрузки.

Кнопка обязана делать три вещи сразу: сказать пользователю, что запрос
остановлен, пометить сессию отменённой (её читает progress hook yt-dlp) и
убрать за собой временные файлы.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from utils import telegram_utils
from utils.cancellation import forget_cancellation, is_cancelled


SESSION_ID = "7_cancel-flow"
TOKEN = "tok12345"

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def clean_registry():
    yield
    forget_cancellation(SESSION_ID)


@pytest.fixture
def context():
    return SimpleNamespace(
        user_data={
            "sessions": {
                TOKEN: {
                    "url": "https://youtu.be/abc",
                    "video_info": {"title": "видео"},
                    "session_id": SESSION_ID,
                    "platform": "youtube",
                    "formats": {},
                    "created_at": 0.0,
                }
            }
        }
    )


def _query(edit):
    return SimpleNamespace(
        edit_message_text=edit,
        from_user=SimpleNamespace(id=7),
        message=SimpleNamespace(chat=SimpleNamespace(send_action=AsyncMock())),
    )


def _cancel(context, edit):
    return asyncio.run(
        telegram_utils._handle_main_callback(_query(edit), context, 7, TOKEN, "cancel")
    )


def test_cancel_tells_the_user_what_happened(context):
    edit = AsyncMock()

    _cancel(context, edit)

    assert "Отменено" in edit.await_args_list[0].args[0]


def test_cancel_marks_the_session_so_the_download_stops(context):
    """Без этого признака сервер продолжил бы качать файл в никуда."""
    _cancel(context, AsyncMock())

    assert is_cancelled(SESSION_ID) is True


def test_cancel_removes_the_session(context):
    _cancel(context, AsyncMock())

    assert context.user_data["sessions"] == {}


def test_cancel_cleans_temp_files(context, monkeypatch):
    cleaned = []
    monkeypatch.setattr(telegram_utils, "cleanup_temp_files", cleaned.append)

    _cancel(context, AsyncMock())

    assert cleaned == [SESSION_ID]


def test_finishing_a_cancelled_request_shows_no_menu(context):
    """Пользователь нажал «Отмена», пока шёл разбор ссылки — меню опоздало."""
    context.user_data["sessions"].clear()

    finished = telegram_utils._finish_processing(context, TOKEN, {"title": "x"}, {})

    assert finished is False


def test_finishing_a_live_request_fills_the_session(context):
    finished = telegram_utils._finish_processing(
        context, TOKEN, {"title": "новое"}, {"combined": []}
    )

    assert finished is True
    assert context.user_data["sessions"][TOKEN]["video_info"]["title"] == "новое"
    assert context.user_data["sessions"][TOKEN]["formats"] == {"combined": []}
