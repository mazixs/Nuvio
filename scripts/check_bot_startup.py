"""Проверяет сборку обработчиков бота без сетевого обращения к Telegram."""

from __future__ import annotations

from main import _build_application


def main() -> None:
    app = _build_application()
    handlers = sum(len(group) for group in app.handlers.values())
    if handlers < 10 or app.job_queue is None:
        raise RuntimeError("бот не создал ожидаемые обработчики и планировщик")


if __name__ == "__main__":
    main()
