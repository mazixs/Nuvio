"""Общее состояние обязано меняться без `await` посередине.

Апдейты теперь обрабатываются параллельно (`main.UPDATE_CONCURRENCY`). Гонок
данных в одном event loop не бывает, а логическое переплетение — бывает:
прочитал счётчик → `await` → записал устаревшее значение. Пока все правки
общего состояния синхронны, переплетаться нечему. Тесты закрепляют именно это
свойство, чтобы будущий `await` внутри не проехал незамеченным.
"""

import inspect

import pytest

from utils import callback_fsm, telegram_utils


pytestmark = pytest.mark.unit


class _Context:
    """Минимальный контекст: только то, что читает антиспам."""

    def __init__(self):
        self.user_data: dict = {}


def _has_await(func) -> bool:
    return "await " in inspect.getsource(func)


def test_antispam_check_is_synchronous():
    """Между чтением и записью списка запросов не должно быть точки переключения."""
    assert not inspect.iscoroutinefunction(telegram_utils._check_spam)
    assert not _has_await(telegram_utils._check_spam)


def test_session_store_mutations_are_synchronous():
    """Создание и удаление сессии — атомарные операции для event loop."""
    for method in (
        callback_fsm.SessionStore.create,
        callback_fsm.SessionStore.remove,
    ):
        assert not inspect.iscoroutinefunction(method), method.__name__
        assert not _has_await(method), method.__name__


def test_antispam_counts_every_request():
    """Ни один запрос не теряется: последний за окно упирается в лимит."""
    context = _Context()

    verdicts = [
        telegram_utils._check_spam(1, context, now=100.0 + index * 0.1)
        for index in range(telegram_utils._SPAM_REQUEST_LIMIT)
    ]

    assert verdicts[:-1] == [False] * (telegram_utils._SPAM_REQUEST_LIMIT - 1)
    assert verdicts[-1] is True


def test_two_users_do_not_share_antispam_state():
    """Счётчики живут в user_data, поэтому один пользователь не блокирует другого."""
    first, second = _Context(), _Context()
    for index in range(telegram_utils._SPAM_REQUEST_LIMIT):
        telegram_utils._check_spam(1, first, now=100.0 + index * 0.1)

    assert telegram_utils._check_spam(2, second, now=100.5) is False
