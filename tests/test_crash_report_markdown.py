"""Краш-репорт уходит админам как markdown-файл и не ломается на своём же тексте.

В репорт попадают тексты исключений и строки исходников, поэтому проверяется не
только разметка, но и устойчивость: вертикальная черта не должна закрывать
столбец таблицы, а обратные кавычки внутри traceback — разрывать блок кода.
"""

import asyncio

import pytest

from utils import cookie_health, telegram_utils


class _DummyBot:
    def __init__(self):
        self.sent_documents = []

    async def send_document(self, chat_id, document, filename, caption):
        self.sent_documents.append(
            {
                "chat_id": chat_id,
                "text": document.getvalue().decode("utf-8"),
                "filename": filename,
                "caption": caption,
            }
        )


def _send(monkeypatch, exc, *, url="https://www.youtube.com/watch?v=UyXbRBxS2RI"):
    bot = _DummyBot()
    monkeypatch.setattr(telegram_utils, "_bot_instance", bot)
    monkeypatch.setattr(telegram_utils, "ADMIN_IDS", [1])
    monkeypatch.setattr(
        telegram_utils,
        "check_cookie_health",
        lambda platform: cookie_health.CookieHealthResult(
            platform, "valid", "auth cookies are active", 0.0, 2, 2
        ),
    )
    asyncio.run(
        telegram_utils._log_platform_failure(
            platform="youtube",
            stage="format_download",
            url=url,
            error_code="YT-MEDIA_FO-ABC123",
            exc=exc,
            session_id="42_f1336a68",
        )
    )
    return bot.sent_documents[0]


@pytest.mark.unit
def test_report_is_a_markdown_file(monkeypatch):
    document = _send(monkeypatch, RuntimeError("boom"))

    assert document["filename"] == "crash_YT-MEDIA_FO-ABC123.md"
    assert document["text"].startswith("# 🔴 Краш-репорт — `YT-MEDIA_FO-ABC123`")


@pytest.mark.unit
def test_report_carries_facts_as_a_table(monkeypatch):
    text = _send(monkeypatch, RuntimeError("boom"))["text"]

    assert "| Поле | Значение |" in text
    assert "| Платформа | youtube |" in text
    assert "| Этап | format_download |" in text
    assert "| Сессия | `42_f1336a68` |" in text
    assert "| Cookies | valid |" in text
    assert "<https://www.youtube.com/watch?v=UyXbRBxS2RI>" in text
    # Время нужно, чтобы искать событие в `logs/bot.log` по метке, а не по глазам.
    assert "| Время (UTC) | " in text


@pytest.mark.unit
def test_exception_and_traceback_live_in_code_blocks(monkeypatch):
    text = _send(monkeypatch, RuntimeError("boom"))["text"]

    assert "## Исключение" in text
    assert "```text\nRuntimeError: boom\n```" in text
    assert "## Traceback" in text
    assert "```python" in text


@pytest.mark.unit
def test_pipe_in_message_does_not_break_the_table(monkeypatch):
    text = _send(monkeypatch, RuntimeError("a | b"), url="https://x.test/a|b")["text"]

    header_index = text.index("| Поле | Значение |")
    table = text[header_index : text.index("## Исключение")]
    for line in table.strip().splitlines():
        assert line.count("|") - line.count("\\|") == 3, line


@pytest.mark.unit
def test_backticks_in_message_do_not_break_the_code_block(monkeypatch):
    text = _send(monkeypatch, RuntimeError("```oops```"))["text"]

    assert "````text\nRuntimeError: ```oops```\n````" in text


@pytest.mark.unit
def test_missing_url_and_session_are_marked_plainly(monkeypatch):
    bot = _DummyBot()
    monkeypatch.setattr(telegram_utils, "_bot_instance", bot)
    monkeypatch.setattr(telegram_utils, "ADMIN_IDS", [1])

    asyncio.run(
        telegram_utils._notify_admins_crash(
            error_code="BOT-UNKNOWN-ABC123",
            platform="bot",
            stage="callback",
            url=None,
            exc=RuntimeError("boom"),
        )
    )

    text = bot.sent_documents[0]["text"]
    assert "| Ссылка | N/A |" in text
    assert "| Сессия | N/A |" in text
