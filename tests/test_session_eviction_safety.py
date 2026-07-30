"""Появление новой сессии не должно уничтожать работу старой.

Замерено на проде 2026-07-30. Пользователь прислал 12 ссылок; `SessionStore`
держит пять сессий и при создании шестой удалял временный каталог самой старой —
не спрашивая, работает ли она прямо сейчас:

    08:16:59  сессия b2ae8571: файл скачан
    08:17:08  отправка: File ... can't be opened for reading   ← каталог снесён
    08:17:44  сессия 107caa79: Unable to rename file: No such file or directory
                                                          ← снесли на скачивании

Наружу это выходило как `TypeError: Object of type PosixPath is not JSON
serializable`: PTB превращает путь в ссылку только если файл существует, иначе
отдаёт объект как есть, и он падает на сериализации.

До параллельной обработки апдейтов баг был недостижим — пятерых сессий за время
одной загрузки не набиралось, потому что загрузка держала обработку целиком.
"""

import pytest

from utils.callback_fsm import SessionStore


pytestmark = pytest.mark.unit


def _store(user_data, tokens, *, max_active=2, busy=(), hard_limit=25):
    return SessionStore(
        user_data,
        max_active=max_active,
        hard_limit=hard_limit,
        token_factory=iter(tokens).__next__,
        is_disposable=lambda session_id: session_id not in busy,
        clock=iter(float(index) for index in range(1, 100)).__next__,
    )


def _fill(store, count):
    return [
        store.create(
            url=str(index),
            video_info={},
            session_id=f"s{index}",
            platform="youtube",
            formats={},
        )
        for index in range(1, count + 1)
    ]


def test_busy_session_survives_a_new_one(tmp_path):
    """Ровно замеренный случай: старая сессия качает, приходит новая ссылка."""
    user_data = {}
    store = _store(user_data, ("one", "two", "three"), busy={"s1"})

    tokens = _fill(store, 3)

    assert store.get("one") is not None, "занятую сессию вытеснили"
    assert store.get(tokens[2]) is not None


def test_eviction_never_touches_files(tmp_path):
    """Вытеснение вообще не имеет права удалять файлы.

    Раньше оно вызывало `cleanup_temp_files(session_id)`. Даже с проверкой
    занятости это опасная связь: удаление файлов принадлежит владельцу работы,
    а не соседней сессии.
    """
    import inspect

    parameters = inspect.signature(SessionStore.__init__).parameters
    source = inspect.getsource(SessionStore)

    assert "cleanup_session" not in parameters
    # Именно вызов, а не упоминание: в docstring причина описана словами.
    assert "cleanup_temp_files(" not in source


def test_idle_sessions_are_still_evicted():
    """Брошенные меню обязаны вытесняться, иначе хранилище растёт без предела."""
    user_data = {}
    store = _store(user_data, ("one", "two", "three"), busy=())

    _fill(store, 3)

    assert store.get("one") is None
    assert len(store.data) == 2


def test_oldest_disposable_goes_first_not_the_oldest_overall():
    """Занятая сессия пропускается, вытесняется следующая по возрасту."""
    user_data = {}
    store = _store(user_data, ("one", "two", "three"), busy={"s1"}, max_active=2)

    _fill(store, 3)

    assert store.get("one") is not None, "занятую вытеснили вместо свободной"
    assert store.get("two") is None, "свободная сессия должна была уйти"


def test_hard_limit_bounds_growth_even_when_everything_is_busy():
    """Страховка от бесконечного роста, если всё занято.

    Утечка одного каталога хуже, чем неограниченная память: остатки стирает
    уборка при старте процесса.
    """
    user_data = {}
    busy = {f"s{index}" for index in range(1, 10)}
    store = _store(
        user_data,
        tuple(f"t{index}" for index in range(1, 10)),
        max_active=2,
        hard_limit=4,
        busy=busy,
    )

    _fill(store, 9)

    assert len(store.data) == 4
