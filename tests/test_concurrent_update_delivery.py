"""Нажатие кнопки обязано доходить до хэндлера, пока идёт долгая задача.

Замерено на PTB 22.8: у `Application` по умолчанию
`max_concurrent_updates = 1`, и `__update_fetcher` ждёт завершения текущего
апдейта, прежде чем взять следующий. Пока `download_content` висит на
`run_blocking`, нажатие «Отменить» лежит в очереди:

    0.0с  обработчик длинной задачи начал
    0.3с  нажата кнопка «Отменить»
    1.5с  обработчик длинной задачи кончил
    1.5с  ОТМЕНА обработана        ← уже впустую

Механизм отмены при этом исправен — до него не доходит сигнал. Остальные тесты
отмены вызывают `button_callback` напрямую и поэтому проверяют механизм, но не
доставку.
"""

import asyncio
import contextlib

import pytest
from telegram import User
from telegram.ext import ApplicationBuilder, TypeHandler

import main


pytestmark = pytest.mark.integration

LONG_TASK_SECONDS = 0.6
PRESS_AFTER_SECONDS = 0.1


class _LongTask:
    """Апдейт, который обрабатывается долго, как скачивание."""


class _Press:
    """Апдейт от кнопки, который должен обработаться не дожидаясь первого."""


def _run_two_updates(concurrency) -> dict[str, float]:
    """Возвращает моменты событий относительно старта долгой задачи."""
    marks: dict[str, float] = {}

    async def scenario() -> None:
        builder = ApplicationBuilder().token("123:ABC")
        if concurrency is not None:
            builder = builder.concurrent_updates(concurrency)
        app = builder.build()
        # `initialize()` ходит в сеть за `get_me`, а нам нужен только фетчер.
        # В параллельном режиме фетчер называет задачу через `bot.id`, поэтому
        # подставляем кэш `get_me` вместо сетевого запроса.
        app._initialized = True
        app.bot._bot_user = User(id=1, first_name="nuvio-test", is_bot=True)

        started = asyncio.get_running_loop().time()

        async def long_task(_update, _context) -> None:
            marks["задача началась"] = asyncio.get_running_loop().time() - started
            await asyncio.sleep(LONG_TASK_SECONDS)
            marks["задача кончилась"] = asyncio.get_running_loop().time() - started

        async def press(_update, _context) -> None:
            marks["нажатие обработано"] = asyncio.get_running_loop().time() - started

        app.add_handler(TypeHandler(_LongTask, long_task))
        app.add_handler(TypeHandler(_Press, press))

        fetcher = asyncio.create_task(app._update_fetcher())
        await app.update_queue.put(_LongTask())
        await asyncio.sleep(PRESS_AFTER_SECONDS)
        await app.update_queue.put(_Press())
        await asyncio.sleep(LONG_TASK_SECONDS + 0.4)
        fetcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await fetcher

    asyncio.run(scenario())
    return marks


def test_press_is_handled_before_the_long_task_finishes():
    """Настроенная параллельность обязана доставлять нажатие сразу."""
    marks = _run_two_updates(main.UPDATE_CONCURRENCY)

    assert "нажатие обработано" in marks, "нажатие не дошло до хэндлера"
    assert marks["нажатие обработано"] < marks["задача кончилась"], (
        f"нажатие обработано на {marks['нажатие обработано']:.2f}с, "
        f"а задача кончилась на {marks['задача кончилась']:.2f}с — "
        "значит апдейт ждал в очереди"
    )


def test_default_configuration_is_the_one_that_was_broken():
    """Фиксирует саму причину: без настройки нажатие ждёт конца задачи.

    Тест страхует от «починки», которая на самом деле ничего не меняет: если
    однажды PTB сменит поведение по умолчанию, он упадёт и заставит перечитать
    план выпуска.
    """
    marks = _run_two_updates(None)

    assert marks["нажатие обработано"] >= marks["задача кончилась"]


def test_concurrency_limit_leaves_room_for_navigation():
    """Предел обязан быть выше числа воркеров скачивания.

    Иначе восемь занятых загрузок съедают все слоты, и девятый апдейт —
    нажатие «Отменить» — снова ждёт в очереди.
    """
    from config import DOWNLOAD_WORKERS

    assert main.UPDATE_CONCURRENCY > DOWNLOAD_WORKERS
