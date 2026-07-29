"""Отметка «отправляет видео…» не должна пропадать на длинной отправке.

Прежний цикл выходил навсегда при первой же ошибке отправки:

    except telegram.error.TelegramError as error:
        logger.debug(...)
        return

Один сетевой сбой за десять минут выгрузки — и в шапке чата больше ничего нет,
хотя работа идёт. Именно из этого выросла жалоба «не понимаешь, сломалось или
видео всё-таки придёт». Исчерпание пула соединений тут не при чём:
`connection_pool_size` по умолчанию 256.
"""

import asyncio

import pytest
import telegram

from utils import telegram_utils


pytestmark = pytest.mark.unit


class _Chat:
    """Чат, у которого первые `failures` отправок падают."""

    def __init__(self, failures: int):
        self._failures = failures
        self.sent = 0

    async def send_action(self, action: str) -> None:
        self.sent += 1
        if self.sent <= self._failures:
            raise telegram.error.TimedOut()


def _pulses_during(chat, seconds: float) -> int:
    async def scenario() -> None:
        async with telegram_utils._pulsing_chat_action(chat, "upload_video"):
            await asyncio.sleep(seconds)

    asyncio.run(scenario())
    return chat.sent


def test_pulse_continues_after_a_failed_send(monkeypatch):
    """Сбой отправки не повод бросать отметку до конца работы."""
    monkeypatch.setattr(telegram_utils, "_CHAT_ACTION_REFRESH_SECONDS", 0.05)
    chat = _Chat(failures=1)

    sent = _pulses_during(chat, 0.3)

    assert sent > 1, "после ошибки пульс больше не пытался"


def test_pulse_does_not_hot_loop_when_every_send_fails(monkeypatch):
    """Пауза обязана соблюдаться и на ошибках, иначе это busy-loop."""
    monkeypatch.setattr(telegram_utils, "_CHAT_ACTION_REFRESH_SECONDS", 0.05)
    chat = _Chat(failures=1000)

    sent = _pulses_during(chat, 0.3)

    assert sent <= 8, f"за 0.3с при паузе 0.05с не может быть {sent} попыток"


def test_pulse_stops_when_the_work_is_over(monkeypatch):
    """Отметка живёт ровно столько, сколько работа."""
    monkeypatch.setattr(telegram_utils, "_CHAT_ACTION_REFRESH_SECONDS", 0.05)
    chat = _Chat(failures=0)

    sent = _pulses_during(chat, 0.12)
    after_exit = chat.sent

    asyncio.run(asyncio.sleep(0.2))

    assert sent >= 1
    assert chat.sent == after_exit, "пульс продолжился после выхода из блока"


def test_disabled_pulse_sends_nothing():
    """На дешёвых действиях отметка не нужна и не должна шуметь."""
    chat = _Chat(failures=0)

    async def scenario() -> None:
        async with telegram_utils._pulsing_chat_action(chat, "upload_video", False):
            await asyncio.sleep(0.05)

    asyncio.run(scenario())

    assert chat.sent == 0
