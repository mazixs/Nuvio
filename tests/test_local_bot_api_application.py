"""Тесты настройки клиента локального Telegram Bot API."""

import pytest

import main


class _BuilderRecorder:
    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name):
        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self

        return method

    def called_with(self, name, *args):
        return any(
            call_name == name and call_args == args
            for call_name, call_args, _call_kwargs in self.calls
        )


@pytest.mark.unit
def test_cloud_builder_does_not_override_api_urls(monkeypatch):
    builder = _BuilderRecorder()
    monkeypatch.setattr(main, "TELEGRAM_LOCAL_MODE", False, raising=False)

    result = main._configure_application_builder(builder)

    assert result is builder
    assert not any(name == "local_mode" for name, _args, _kwargs in builder.calls)
    assert not any(name == "base_url" for name, _args, _kwargs in builder.calls)
    assert not any(name == "base_file_url" for name, _args, _kwargs in builder.calls)


@pytest.mark.unit
def test_local_builder_uses_internal_api_and_long_media_timeout(monkeypatch):
    builder = _BuilderRecorder()
    monkeypatch.setattr(main, "TELEGRAM_LOCAL_MODE", True, raising=False)
    monkeypatch.setattr(
        main,
        "TELEGRAM_BOT_API_BASE_URL",
        "http://telegram-bot-api:8081/bot",
        raising=False,
    )
    monkeypatch.setattr(
        main,
        "TELEGRAM_BOT_API_FILE_URL",
        "http://telegram-bot-api:8081/file/bot",
        raising=False,
    )

    main._configure_application_builder(builder)

    assert builder.called_with(
        "base_url", "http://telegram-bot-api:8081/bot"
    )
    assert builder.called_with(
        "base_file_url", "http://telegram-bot-api:8081/file/bot"
    )
    assert builder.called_with("local_mode", True)
    assert builder.called_with("media_write_timeout", 1800.0)
