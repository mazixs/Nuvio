"""Удалённое пользователем сообщение не должно ломать выдачу файла.

Прод дал цепочку из четырёх падений подряд: правка статуса упала с «Message to
edit not found», обработчик этой ошибки попытался написать о ней тем же
способом — и упал так же, затем дважды упал fallback в `button_callback`, и всё
дошло до глобального обработчика. Причина одна: сообщение, которое правим,
пользователь уже удалил, а правка считалась обязанной удаться.

Удалить своё сообщение — законное право пользователя. Файл он всё равно должен
получить: отправка не зависит от того, живо ли сообщение со статусом.
"""

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import telegram

from utils import telegram_utils


pytestmark = pytest.mark.unit

DELETED = telegram.error.BadRequest("Message to edit not found")
NOT_MODIFIED = telegram.error.BadRequest("Message is not modified")
OTHER = telegram.error.BadRequest("Chat not found")


def _query(edit):
    return SimpleNamespace(
        edit_message_text=edit,
        from_user=SimpleNamespace(id=7),
        message=SimpleNamespace(chat=SimpleNamespace(send_action=AsyncMock())),
    )


# --- сама безопасная правка ---------------------------------------------------


def test_deleted_message_is_not_an_error():
    query = _query(AsyncMock(side_effect=DELETED))

    assert asyncio.run(telegram_utils.safe_edit_message_text(query, "текст")) is False


def test_unchanged_message_is_not_an_error():
    query = _query(AsyncMock(side_effect=NOT_MODIFIED))

    assert asyncio.run(telegram_utils.safe_edit_message_text(query, "текст")) is False


def test_other_failures_still_surface():
    """Глушить все BadRequest подряд — значит прятать настоящие поломки."""
    query = _query(AsyncMock(side_effect=OTHER))

    with pytest.raises(telegram.error.BadRequest):
        asyncio.run(telegram_utils.safe_edit_message_text(query, "текст"))


def test_successful_edit_reports_success():
    query = _query(AsyncMock())

    assert asyncio.run(telegram_utils.safe_edit_message_text(query, "текст")) is True


# --- выдача файла -------------------------------------------------------------


def test_file_is_still_sent_when_the_status_message_is_gone(tmp_path, monkeypatch):
    """Главное: пользователь получает видео, а не код ошибки."""
    sent = []
    monkeypatch.setattr(
        telegram_utils,
        "send_single_file",
        AsyncMock(side_effect=lambda *a, **k: sent.append(a[1]) or True),
    )
    monkeypatch.setattr(telegram_utils.asyncio, "sleep", AsyncMock())
    file_path = tmp_path / "видео.mp4"
    file_path.write_bytes(b"data")
    query = _query(AsyncMock(side_effect=DELETED))

    asyncio.run(
        telegram_utils.send_file(
            query,
            file_path,
            "tok12345",
            {"platform": "youtube", "url": "https://youtu.be/x", "session_id": "7_a"},
            SimpleNamespace(user_data={}),
        )
    )

    assert sent == [file_path]


# --- защита от возврата прежнего поведения ------------------------------------


@pytest.mark.parametrize(
    "function",
    [telegram_utils.send_file, telegram_utils.button_callback],
)
def test_crash_path_has_no_raw_edits(function):
    """Обе функции из отчёта о падении обязаны править только безопасно.

    В `button_callback` прямая правка стояла ещё и в обработчике ошибки — тем же
    способом, который только что упал, поэтому падение удваивалось.
    """
    source = inspect.getsource(function)

    assert "query.edit_message_text(" not in source
