# Документация Nuvio

## Содержание

### Руководства

- [Развертывание](guides/deployment.md) -- установка, Docker, systemd, WebUI
- [Конфигурация](guides/configuration.md) -- переменные окружения, cookies, ограничения

### Техническая документация

- [Архитектура](technical/architecture.md) -- модули, поток данных, ключевые паттерны, SQLite WAL
- [FSM-логика](technical/fsm-architecture.md) -- конечные автоматы, узкие места, оптимизации, ICE-приоритизация
- [Runbook: YouTube перестал скачиваться](technical/youtube-download-runbook.md) -- инцидент 18.08.2026, ловушки ложноотрицательных проб, процедура разбора, обновление пина yt-dlp
- [Коды ошибок](error-codes.md) -- формат кодов, префиксы (YT/TT/IG/RU/VK/TG/FILE/BOT), категории, поиск в логах

### Разработка

- [Участие в разработке](development/contributing.md) -- окружение, тесты, соглашения по коду

### Устранение неполадок

- [Частые проблемы](troubleshooting/common-issues.md) -- запуск, платформы, файлы, кэш
