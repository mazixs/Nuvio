# Справочник по конфигурации

Единственный шаблон настроек — `.env.example`. Рабочую копию храните в
`.secrets/.env`; она исключена из системы контроля версий.

## Обязательные переменные

| Переменная | Где используется | Описание |
|---|---|---|
| `TELEGRAM_TOKEN` | бот | Токен от @BotFather |
| `ADMIN_IDS` | бот | ID администраторов через запятую |
| `TELEGRAM_API_ID` | Docker | ID приложения с my.telegram.org |
| `TELEGRAM_API_HASH` | Docker | Hash приложения с my.telegram.org |

`TELEGRAM_API_ID` и `TELEGRAM_API_HASH` создаются в разделе **API development
tools** на [my.telegram.org](https://my.telegram.org). Это учётные данные
приложения Telegram, а не токен бота и не пароль пользователя.

## Локальный Telegram Bot API

В `compose.yaml` эти параметры уже заданы и обычно не требуют изменения:

| Переменная | Значение в Compose | Описание |
|---|---|---|
| `TELEGRAM_LOCAL_MODE` | `true` | Разрешает отправку по локальному пути |
| `TELEGRAM_BOT_API_BASE_URL` | `http://telegram-bot-api:8081/bot` | Внутренний адрес API |
| `TELEGRAM_BOT_API_FILE_URL` | `http://telegram-bot-api:8081/file/bot` | Внутренний адрес файлов |
| `TELEGRAM_MAX_FILE_SIZE_MB` | `2000` | Максимальный размер отправляемого файла |
| `TEMP_DIR` | `/app/media` | Общий временный том бота и Bot API |

При прямом запуске Python локальный режим выключен, используются адреса
`api.telegram.org`, а лимит принудительно ограничен 50 МБ.

## WebUI

| Переменная | По умолчанию | Описание |
|---|---|---|
| `WEB_USERNAME` | `admin` | Логин |
| `WEB_PASSWORD` | `changeme` | Пароль, обязательно сменить |
| `WEB_SECRET_KEY` | случайный | Ключ подписи сессий |
| `WEB_PORT` | `8080` | Опубликованный порт |
| `FAIL2BAN_RETRIES` | `5` | Попыток до блокировки |
| `FAIL2BAN_TIME` | `10m` | Время блокировки |

## Загрузка и обработка

| Переменная | По умолчанию | Описание |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Уровень логирования |
| `DOWNLOAD_WORKERS` | `8` | Потоки блокирующих задач |
| `BLOCKING_TASK_TIMEOUT` | `600` | Тайм-аут задачи, секунд |
| `YTDLP_AUTO_UPDATE` | `true` | Обновлять yt-dlp при старте |
| `YTDLP_RELEASE_CHANNEL` | `nightly` | `stable`, `nightly` или `master` |
| `YTDLP_AUTO_UPDATE_TIMEOUT` | `240` | Тайм-аут обновления |
| `YTDLP_CLI_FALLBACK` | `true` | Использовать CLI как запасной путь |
| `YTDLP_CLI_TIMEOUT` | `900` | Тайм-аут CLI |

## Cookies

| Переменная | Путь по умолчанию |
|---|---|
| `YOUTUBE_COOKIES_FILE` | `.secrets/www.youtube.com_cookies.txt` |
| `INSTAGRAM_COOKIES_FILE` | `.secrets/www.instagram.com_cookies.txt` |
| `TIKTOK_COOKIES_FILE` | `.secrets/www.tiktok.com_cookies.txt` |

Cookies необязательны для открытых материалов. Их также можно обновлять через
административную команду `/admin`.

## Хранение

- `bot-data` хранит SQLite-базы и переживает перезапуск контейнеров.
- `telegram-bot-api-data` хранит служебное состояние локального Bot API.
- `shared-media` используется только для обработки и отправки. Файлы удаляются
  после успешной или неуспешной отправки; дополнительная очистка выполняется при
  запуске и остановке бота.
- `logs/` и `.secrets/` подключаются с хоста.
