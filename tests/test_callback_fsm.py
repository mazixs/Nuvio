"""Тесты типизированного ядра callback FSM."""

from utils.callback_fsm import CallbackEvent, SessionStore


def test_callback_event_parses_main_action():
    event = CallbackEvent.parse("s|abc12345|main|back")

    assert event == CallbackEvent(
        scope="main",
        action="back",
        session_token="abc12345",
    )


def test_callback_event_parses_format_action():
    event = CallbackEvent.parse("s|abc12345|format|combined|137+140")

    assert event == CallbackEvent(
        scope="format",
        action="combined",
        session_token="abc12345",
        value="137+140",
    )


def test_callback_event_rejects_unknown_shape():
    assert CallbackEvent.parse("main|back") is None
    assert CallbackEvent.parse("csi|not-a-number") is None


def test_session_store_evicts_oldest_session():
    """Вытеснение убирает запись, но файлов не касается.

    Прежде оно вызывало `cleanup_temp_files` для вытесненной сессии, и на проде
    это сносило каталог работающей загрузки. Подробности и замеры — в
    tests/test_session_eviction_safety.py.
    """
    user_data = {}
    tokens = iter(("one", "two", "three"))
    store = SessionStore(
        user_data,
        max_active=2,
        token_factory=lambda: next(tokens),
        clock=iter((1.0, 2.0, 3.0)).__next__,
    )

    store.create(url="1", video_info={}, session_id="s1", platform="youtube", formats={})
    second = store.create(
        url="2", video_info={}, session_id="s2", platform="youtube", formats={}
    )
    third = store.create(
        url="3", video_info={}, session_id="s3", platform="youtube", formats={}
    )

    assert store.get("one") is None
    assert store.get(second)["url"] == "2"
    assert store.get(third)["url"] == "3"
