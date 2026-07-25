"""Тесты доведения отмены до загрузчика.

Признак отмены обязан попадать в сами опции yt-dlp: без этого кнопка отменяет
только отправку, а сервер продолжает качать файл, который уже никому не нужен.
"""

import pytest

from utils.cancellation import (
    CancelledByUser,
    forget_cancellation,
    request_cancellation,
)
from utils.ytdlp_common import apply_network_opts


SESSION = "77_cancel"

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def clean_registry():
    yield
    forget_cancellation(SESSION)


def test_options_without_a_session_have_no_hook():
    """Разбор информации о видео идёт без сессии — вешать туда нечего."""
    options = {}

    apply_network_opts(options)

    assert "progress_hooks" not in options


def test_options_with_a_session_carry_the_hook():
    options = {}

    apply_network_opts(options, session_id=SESSION)

    assert len(options["progress_hooks"]) == 1


def test_hook_from_options_stops_a_cancelled_download():
    options = {}
    apply_network_opts(options, session_id=SESSION)
    request_cancellation(SESSION)

    with pytest.raises(CancelledByUser):
        options["progress_hooks"][0]({"status": "downloading"})


def test_existing_hooks_are_kept():
    """Свои хуки загрузчиков затирать нельзя."""
    marker = []
    options = {"progress_hooks": [marker.append]}

    apply_network_opts(options, session_id=SESSION)

    assert len(options["progress_hooks"]) == 2
