#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression tests for audit fixes."""

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import config
from utils import cache_commands
from utils import cookie_health
from utils import cookie_manager
from utils import telegram_utils
from utils import tiktok_instagram_utils
from utils import ytdlp_runtime


class _CapturingYDL:
    """Minimal yt-dlp stub that records options."""

    captured_options: list[dict] = []

    def __init__(self, options):
        self.options = options
        self.__class__.captured_options.append(options.copy())

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def extract_info(self, url, download=False):
        return {
            "title": "stub",
            "uploader": "tester",
            "duration": 42,
            "formats": [
                {
                    "format_id": "18",
                    "format": "720p",
                    "ext": "mp4",
                    "height": 720,
                    "width": 1280,
                    "filesize": 1024,
                    "vcodec": "avc1",
                    "acodec": "aac",
                }
            ],
        }


class _DummyMessage:
    def __init__(self, text: str):
        self.text = text
        self.replies: list[str] = []
        self.reply_calls: list[tuple[str, dict]] = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        self.reply_calls.append((text, kwargs))
        return None


class _DummyQuery:
    def __init__(self, data: str = "", user_id: int = 7):
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.message = SimpleNamespace()
        self.edits: list[tuple[str, dict]] = []
        self.answers: list[tuple[str | None, bool]] = []

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))
        return None

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))
        return None


class _EditableReply:
    def __init__(self):
        self.edits: list[tuple[str, dict]] = []

    async def edit_text(self, text, **kwargs):
        self.edits.append((text, kwargs))
        return None


class _DummyDocument:
    def __init__(self, file_name: str, *, file_size: int = 128, mime_type: str = "text/plain"):
        self.file_name = file_name
        self.file_size = file_size
        self.mime_type = mime_type
        self.get_file_called = False

    async def get_file(self):
        self.get_file_called = True
        raise AssertionError("get_file must not be called in this test")


def test_check_spam_sets_real_timeout():
    context = SimpleNamespace(user_data={})

    assert not telegram_utils._check_spam(1, context, 0.0)
    assert not telegram_utils._check_spam(1, context, 1.0)
    assert not telegram_utils._check_spam(1, context, 2.0)
    assert telegram_utils._check_spam(1, context, 3.0)
    assert context.user_data["spam_blocked_until"] == 13.0

    assert telegram_utils._check_spam(1, context, 8.0)
    assert not telegram_utils._check_spam(1, context, 14.0)


def test_cleanup_user_session_preserves_antispam_state(monkeypatch):
    cleaned_sessions: list[str] = []
    monkeypatch.setattr(
        telegram_utils,
        "cleanup_temp_files",
        lambda session_id: cleaned_sessions.append(session_id),
    )
    context = SimpleNamespace(
        user_data={
            "session_id": "session-1",
            "url": "https://example.com",
            "video_info": {"title": "Example"},
            "formats": {"combined": []},
            "platform": "youtube",
            "recent_requests": [1.0, 2.0],
            "spam_blocked_until": 12.0,
        }
    )

    asyncio.run(telegram_utils._cleanup_user_session(42, context))

    assert cleaned_sessions == ["session-1"]
    assert context.user_data == {
        "recent_requests": [1.0, 2.0],
        "spam_blocked_until": 12.0,
    }


def test_cleanup_specific_session_keeps_other_sessions(monkeypatch):
    cleaned_sessions: list[str] = []
    monkeypatch.setattr(
        telegram_utils,
        "cleanup_temp_files",
        lambda session_id: cleaned_sessions.append(session_id),
    )
    context = SimpleNamespace(user_data={})

    first = telegram_utils._store_session(
        context,
        url="https://example.com/1",
        video_info={"title": "One"},
        session_id="session-1",
        platform="youtube",
        formats={"combined": []},
    )
    second = telegram_utils._store_session(
        context,
        url="https://example.com/2",
        video_info={"title": "Two"},
        session_id="session-2",
        platform="instagram",
        formats={},
    )

    asyncio.run(telegram_utils._cleanup_user_session(42, context, first))

    assert cleaned_sessions == ["session-1"]
    assert telegram_utils._get_session(context, first) is None
    assert telegram_utils._get_session(context, second)["url"] == "https://example.com/2"


def test_process_url_applies_antispam_to_plain_messages(monkeypatch):
    message = _DummyMessage("https://example.com/video")
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=7),
        message=message,
    )
    context = SimpleNamespace(user_data={}, args=[])

    monkeypatch.setattr(telegram_utils, "is_valid_youtube_url", lambda url: False)
    monkeypatch.setattr(telegram_utils, "is_valid_tiktok_url", lambda url: False)
    monkeypatch.setattr(telegram_utils, "is_instagram_audio_url", lambda url: False)
    monkeypatch.setattr(telegram_utils, "is_valid_instagram_url", lambda url: False)

    for _ in range(3):
        asyncio.run(telegram_utils.process_url(update, context, "https://example.com/video"))

    asyncio.run(telegram_utils.process_url(update, context, "https://example.com/video"))

    assert message.replies[-1] == telegram_utils.SPAM_WARNING


def test_build_main_menu_uses_platform_specific_callbacks():
    _, instagram_menu = telegram_utils._build_main_menu(
        "instagram",
        {"title": "Clip", "uploader": "author", "duration": 12},
        "sess1234",
    )

    callbacks = [
        button.callback_data
        for row in instagram_menu.inline_keyboard
        for button in row
    ]

    assert "s|sess1234|main|instagram_download" in callbacks
    assert "s|sess1234|main|instagram_audio" in callbacks
    assert "s|sess1234|main|tg_video" not in callbacks


def test_navigation_callbacks_are_not_rate_limited():
    assert not telegram_utils._should_rate_limit_callback("s|sess1234|main|more")
    assert not telegram_utils._should_rate_limit_callback("s|sess1234|main|back")
    assert telegram_utils._should_rate_limit_callback("s|sess1234|main|tg_video")
    assert telegram_utils._should_rate_limit_callback("s|sess1234|format|best|best")


def test_validate_config_allows_missing_gokapi(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_TOKEN", "telegram-token")
    monkeypatch.setattr(config, "GOKAPI_API_KEY", None)
    monkeypatch.setattr(config, "GOKAPI_BASE_URL", "")

    assert config.validate_config() is True


def test_parse_log_level_and_admin_ids_are_resilient():
    assert config._parse_log_level("debug") == logging.DEBUG
    assert config._parse_log_level("unknown-level") == logging.INFO
    assert config._parse_admin_ids("1, 2, ,oops,3") == [1, 2, 3]


def test_resolve_secret_path_prefers_canonical_location(monkeypatch, tmp_path, caplog):
    legacy = tmp_path / "www.youtube.com_cookies.txt"
    canonical_dir = tmp_path / ".secrets"
    canonical_dir.mkdir()
    canonical = canonical_dir / legacy.name
    legacy.write_text("legacy", encoding="utf-8")
    canonical.write_text("canonical", encoding="utf-8")

    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "SECRETS_DIR", canonical_dir)

    with caplog.at_level(logging.WARNING):
        resolved = config.resolve_secret_path(legacy.name)

    assert resolved == canonical
    assert "Используем" in caplog.text


def test_classify_large_file_delivery_error():
    assert (
        telegram_utils._classify_large_file_delivery_error(
            "Сервер загрузки недоступен: Сервер загрузки больших файлов не настроен"
        )
        == telegram_utils.LARGE_FILE_DELIVERY_UNAVAILABLE
    )


def test_tiktok_info_requests_full_metadata(monkeypatch):
    _CapturingYDL.captured_options.clear()
    monkeypatch.setattr(tiktok_instagram_utils.yt_dlp, "YoutubeDL", _CapturingYDL)
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_smart_retry",
        lambda func, max_attempts=0, context="": func(),
    )
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_get_tiktok_base_configs",
        lambda: [{"quiet": True, "no_warnings": True}],
    )
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "TIKTOK_COOKIES_FILE",
        Path(r"C:\definitely-missing-tiktok-cookies.txt"),
    )

    info = tiktok_instagram_utils.get_tiktok_info("https://www.tiktok.com/@user/video/1")

    assert info["title"] == "stub"
    assert _CapturingYDL.captured_options
    assert "extract_flat" not in _CapturingYDL.captured_options[0]


def test_instagram_info_requests_full_metadata(monkeypatch):
    _CapturingYDL.captured_options.clear()
    monkeypatch.setattr(tiktok_instagram_utils.yt_dlp, "YoutubeDL", _CapturingYDL)
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "INSTAGRAM_COOKIES_FILE",
        Path(r"C:\definitely-missing-instagram-cookies.txt"),
    )

    info = tiktok_instagram_utils.get_instagram_info("https://www.instagram.com/reel/abc123/")

    assert info["title"] == "stub"
    assert _CapturingYDL.captured_options
    assert "extract_flat" not in _CapturingYDL.captured_options[0]


def test_social_cookie_paths_are_absolute():
    assert tiktok_instagram_utils.TIKTOK_COOKIES_FILE.is_absolute()
    assert tiktok_instagram_utils.INSTAGRAM_COOKIES_FILE.is_absolute()


def test_cache_stats_requires_admin(monkeypatch):
    message = _DummyMessage("")
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=2),
        message=message,
    )
    context = SimpleNamespace(args=[])

    monkeypatch.setattr(cache_commands, "ADMIN_IDS", [1])

    asyncio.run(cache_commands.stats_command(update, context))

    assert message.replies == ["🔒 Эта команда доступна только администраторам"]


def test_non_admin_document_upload_is_rejected_without_download(monkeypatch):
    document = _DummyDocument("www.youtube.com_cookies.txt")
    message = _DummyMessage("")
    message.document = document
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=2),
        message=message,
    )
    context = SimpleNamespace(user_data={})

    monkeypatch.setattr(cookie_manager, "ADMIN_IDS", [1])

    asyncio.run(cookie_manager.handle_document_upload(update, context))

    assert message.replies == [cookie_manager.NON_ADMIN_DOCUMENT_MESSAGE]
    assert document.get_file_called is False


def test_admin_document_upload_requires_armed_mode(monkeypatch):
    document = _DummyDocument("www.youtube.com_cookies.txt")
    message = _DummyMessage("")
    message.document = document
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        message=message,
    )
    context = SimpleNamespace(user_data={})

    monkeypatch.setattr(cookie_manager, "ADMIN_IDS", [1])

    asyncio.run(cookie_manager.handle_document_upload(update, context))

    assert message.replies == [cookie_manager.ADMIN_UPLOAD_REQUIRED_MESSAGE]
    assert document.get_file_called is False


def test_admin_callback_arms_specific_cookie_upload(monkeypatch):
    query = _DummyQuery("admin|cookies|upload|youtube", user_id=1)
    update = SimpleNamespace(callback_query=query)
    context = SimpleNamespace(user_data={})

    monkeypatch.setattr(cookie_manager, "ADMIN_IDS", [1])

    asyncio.run(cookie_manager.handle_admin_callback(update, context))

    assert context.user_data[cookie_manager.ADMIN_UPLOAD_TARGET_KEY] == "www.youtube.com_cookies.txt"
    assert "Expected file: www.youtube.com_cookies.txt" in query.edits[-1][0]


def test_admin_command_shows_cookie_panel(monkeypatch):
    message = _DummyMessage("")
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        message=message,
    )
    context = SimpleNamespace(user_data={cookie_manager.ADMIN_UPLOAD_TARGET_KEY: "stale.txt"})

    monkeypatch.setattr(cookie_manager, "ADMIN_IDS", [1])

    asyncio.run(cookie_manager.admin_command(update, context))

    assert cookie_manager.ADMIN_UPLOAD_TARGET_KEY not in context.user_data
    assert "Cookie status:" in message.reply_calls[-1][0]
    assert "reply_markup" in message.reply_calls[-1][1]


def test_cookie_health_reports_missing_file(monkeypatch, tmp_path):
    missing_path = tmp_path / "missing.txt"
    monkeypatch.setitem(cookie_health.COOKIE_PATHS, "youtube", missing_path)
    cookie_health._COOKIE_HEALTH_CACHE.clear()

    result = cookie_health.check_cookie_health("youtube", force=True)

    assert result.status == "missing"


def test_cookie_health_detects_expired_auth_cookie(monkeypatch, tmp_path):
    cookie_file = tmp_path / "youtube.txt"
    cookie_file.write_text(
        "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1\tSID\texpired\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(cookie_health.COOKIE_PATHS, "youtube", cookie_file)
    cookie_health._COOKIE_HEALTH_CACHE.clear()

    result = cookie_health.check_cookie_health("youtube", force=True)

    assert result.status == "expired"


def test_admin_callback_runs_cookie_health_check(monkeypatch):
    query = _DummyQuery("admin|cookies|check", user_id=1)
    update = SimpleNamespace(callback_query=query)
    context = SimpleNamespace(user_data={})

    monkeypatch.setattr(cookie_manager, "ADMIN_IDS", [1])
    monkeypatch.setattr(
        cookie_manager,
        "check_all_cookie_health",
        lambda: {
            "youtube": cookie_health.CookieHealthResult("youtube", "valid", "probe ok", 0.0, 3, 3),
            "instagram": cookie_health.CookieHealthResult("instagram", "expired", "all auth cookies are expired", 0.0, 2, 0),
            "tiktok": cookie_health.CookieHealthResult("tiktok", "rate_limited", "platform temporarily rate-limited the validation probe", 0.0, 2, 2),
        },
    )

    asyncio.run(cookie_manager.handle_admin_callback(update, context))

    assert "Cookie health check" in query.edits[-1][0]
    assert "YouTube: valid - probe ok" in query.edits[-1][0]


def test_search_cache_requires_admin(monkeypatch):
    message = _DummyMessage("")
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=2),
        message=message,
    )
    context = SimpleNamespace(args=["example"])

    monkeypatch.setattr(cache_commands, "ADMIN_IDS", [1])

    asyncio.run(cache_commands.search_cache_command(update, context))

    assert message.replies == ["🔒 Эта команда доступна только администраторам"]


def test_search_cache_escapes_markdown(monkeypatch):
    message = _DummyMessage("")
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=7),
        message=message,
    )
    context = SimpleNamespace(args=["query_[1]"])

    monkeypatch.setattr(cache_commands, "ADMIN_IDS", [7])
    monkeypatch.setattr(
        cache_commands.telegram_cache,
        "search_by_title",
        lambda query, limit=10: [
            SimpleNamespace(
                platform="youtube",
                title="Title_[1]",
                cached_at=datetime(2026, 1, 1),
            )
        ],
    )

    asyncio.run(cache_commands.search_cache_command(update, context))

    text, kwargs = message.reply_calls[-1]
    assert kwargs["parse_mode"] == "Markdown"
    assert r"query\_\[1\]" in text
    assert r"Title\_\[1\]" in text


def test_send_file_keeps_session_on_send_failure(monkeypatch):
    query = _DummyQuery()
    context = SimpleNamespace(user_data={})
    session_token = telegram_utils._store_session(
        context,
        url="https://example.com/1",
        video_info={"title": "One"},
        session_id="session-1",
        platform="youtube",
        formats={"combined": []},
    )
    session_data = telegram_utils._get_session(context, session_token)

    async def fake_send_single_file(*args, **kwargs):
        return False

    monkeypatch.setattr(telegram_utils, "send_single_file", fake_send_single_file)

    asyncio.run(
        telegram_utils.send_file(
            query,
            Path("fake.mp4"),
            session_token,
            session_data,
            context,
        )
    )

    assert telegram_utils._get_session(context, session_token) is not None


def test_process_url_youtube_does_not_short_circuit_by_video_cache(monkeypatch):
    processing_messages: list[_EditableReply] = []
    message = _DummyMessage("https://youtu.be/abc123def45")

    async def fake_reply_text(text, **kwargs):
        if text == telegram_utils.PROCESSING_MESSAGE:
            reply = _EditableReply()
            processing_messages.append(reply)
            return reply
        message.replies.append(text)
        message.reply_calls.append((text, kwargs))
        return None

    message.reply_text = fake_reply_text
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=7),
        message=message,
    )
    context = SimpleNamespace(user_data={}, args=[])

    async def fail_if_cached(*args, **kwargs):
        raise AssertionError("YouTube path must not short-circuit through cached video before format selection")

    async def fake_run_blocking(func, *args, **kwargs):
        return {"title": "Stub title", "uploader": "Tester", "duration": 30, "formats": []}

    monkeypatch.setattr(telegram_utils, "_try_send_cached", fail_if_cached)
    monkeypatch.setattr(telegram_utils, "is_valid_youtube_url", lambda url: True)
    monkeypatch.setattr(telegram_utils, "get_available_formats", lambda video_info: {"combined": [], "video_only": [], "audio_only": []})
    monkeypatch.setattr(telegram_utils, "create_temp_dir", lambda session_id: None)
    monkeypatch.setattr(telegram_utils, "run_blocking", fake_run_blocking)

    asyncio.run(telegram_utils.process_url(update, context, message.text))

    assert processing_messages
    assert "Stub title" in processing_messages[-1].edits[-1][0]


def test_process_url_tiktok_shows_menu_not_cache(monkeypatch):
    """TikTok URL должен показывать меню, а не отправлять из кэша напрямую."""
    message = _DummyMessage("https://www.tiktok.com/@user/video/1")
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=7),
        message=message,
    )
    context = SimpleNamespace(user_data={}, args=[])
    cache_called = False

    async def fake_try_send_cached(update, url, user_id, cache_format_id, platform="video"):
        nonlocal cache_called
        cache_called = True
        return True

    monkeypatch.setattr(telegram_utils, "_try_send_cached", fake_try_send_cached)
    monkeypatch.setattr(telegram_utils, "is_valid_youtube_url", lambda url: False)
    monkeypatch.setattr(telegram_utils, "is_valid_tiktok_url", lambda url: True)
    monkeypatch.setattr(telegram_utils, "is_instagram_audio_url", lambda url: False)
    monkeypatch.setattr(telegram_utils, "is_valid_instagram_url", lambda url: False)

    # process_url больше не вызывает _try_send_cached для TikTok (кэш перенесён в callback)
    # Поэтому нужно мокнуть reply_text (показ меню) и get_tiktok_info
    async def fake_reply_text(text, **kwargs):
        return SimpleNamespace(edit_text=fake_edit_text)

    async def fake_edit_text(text, **kwargs):
        pass

    message.reply_text = fake_reply_text

    async def fake_get_tiktok_info(url):
        return {"title": "Test", "uploader": "User", "duration": 10}

    monkeypatch.setattr(telegram_utils, "get_tiktok_info", fake_get_tiktok_info)
    monkeypatch.setattr(telegram_utils, "run_blocking", lambda func, *a, **kw: func(*a))

    asyncio.run(telegram_utils.process_url(update, context, message.text))

    assert not cache_called, "process_url не должен проверять кэш для TikTok — это делает callback handler"


def test_send_single_file_persists_explicit_cache_key(monkeypatch, tmp_path):
    file_path = tmp_path / "clip.mp4"
    file_path.write_bytes(b"video")
    stored = []

    async def fake_reply_video(*args, **kwargs):
        return SimpleNamespace(
            video=SimpleNamespace(
                file_id="file-1",
                file_unique_id="uniq-1",
                file_size=4,
                duration=9,
            )
        )

    query = _DummyQuery()
    query.message.reply_video = fake_reply_video
    monkeypatch.setattr(telegram_utils.telegram_cache, "set", lambda cached: stored.append(cached))

    result = asyncio.run(
        telegram_utils.send_single_file(
            query,
            file_path,
            "sess-token",
            {
                "url": "https://youtu.be/abc123def45",
                "video_info": {"title": "Cached clip"},
                "platform": "youtube",
            },
            cache_format_id="tg_video",
        )
    )

    assert result is True
    assert stored
    assert stored[0].format_id == "tg_video"


def test_build_yt_dlp_upgrade_command_uses_release_channel():
    stable = ytdlp_runtime.build_yt_dlp_upgrade_command("stable")
    nightly = ytdlp_runtime.build_yt_dlp_upgrade_command("nightly")
    master = ytdlp_runtime.build_yt_dlp_upgrade_command("master")

    assert stable[-1] == "yt-dlp[default]"
    assert "--pre" in nightly
    assert "master.tar.gz" in master[-1]
