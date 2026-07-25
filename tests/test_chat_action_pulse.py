"""Тесты отметки активности в шапке чата.

Это единственная анимация, доступная боту: Telegram гасит отметку через пять
секунд, поэтому её приходится обновлять, пока идёт работа. Правка текста
сообщения ради «крутилки» стоила бы запроса на каждый кадр и затирала бы
статусы.
"""

import asyncio

import pytest
import telegram

from utils import telegram_utils


pytestmark = pytest.mark.unit


class _Chat:
    def __init__(self, fail: bool = False):
        self.actions: list[str] = []
        self._fail = fail

    async def send_action(self, action):
        if self._fail:
            raise telegram.error.TelegramError("чат недоступен")
        self.actions.append(str(action))


def test_video_work_shows_video_upload():
    assert "video" in telegram_utils._chat_action_for("tiktok_download")
    assert "video" in telegram_utils._chat_action_for("tg_video")


def test_audio_and_subtitles_show_document_upload():
    """«Отправляет видео» на аудиодорожке или субтитрах вводило бы в заблуждение."""
    assert "document" in telegram_utils._chat_action_for("tiktok_audio")
    assert "document" in telegram_utils._chat_action_for("audio_m4a")
    assert "document" in telegram_utils._chat_action_for("subtitles")


def test_action_is_repeated_while_work_goes_on(monkeypatch):
    monkeypatch.setattr(telegram_utils, "_CHAT_ACTION_REFRESH_SECONDS", 0.01)
    chat = _Chat()

    async def scenario():
        async with telegram_utils._pulsing_chat_action(chat, "upload_video"):
            await asyncio.sleep(0.05)

    asyncio.run(scenario())

    assert len(chat.actions) >= 2, chat.actions


def test_pulse_stops_when_work_is_done(monkeypatch):
    monkeypatch.setattr(telegram_utils, "_CHAT_ACTION_REFRESH_SECONDS", 0.01)
    chat = _Chat()

    async def scenario():
        async with telegram_utils._pulsing_chat_action(chat, "upload_video"):
            await asyncio.sleep(0.02)
        sent_at_exit = len(chat.actions)
        await asyncio.sleep(0.05)
        return sent_at_exit, len(chat.actions)

    sent_at_exit, sent_later = asyncio.run(scenario())

    assert sent_at_exit == sent_later


def test_disabled_pulse_sends_nothing():
    """Навигация по меню мгновенна — отметка активности там только шумит."""
    chat = _Chat()

    async def scenario():
        async with telegram_utils._pulsing_chat_action(
            chat, "upload_video", enabled=False
        ):
            await asyncio.sleep(0.01)

    asyncio.run(scenario())

    assert chat.actions == []


def test_unavailable_chat_does_not_break_the_work(monkeypatch):
    """Отметка — украшение: её отказ не должен ронять саму загрузку."""
    monkeypatch.setattr(telegram_utils, "_CHAT_ACTION_REFRESH_SECONDS", 0.01)
    done = False

    async def scenario():
        nonlocal done
        async with telegram_utils._pulsing_chat_action(_Chat(fail=True), "upload_video"):
            await asyncio.sleep(0.03)
            done = True

    asyncio.run(scenario())

    assert done is True
