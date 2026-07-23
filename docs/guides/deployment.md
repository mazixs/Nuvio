# Развёртывание Nuvio

## Требования

- Docker Engine с Compose v2
- токен бота от [@BotFather](https://t.me/BotFather)
- `API_ID` и `API_HASH` приложения с [my.telegram.org](https://my.telegram.org)
- достаточно свободного места для временной обработки файлов до 2 ГБ

`API_ID` и `API_HASH` создаются в разделе **API development tools**. Они не
заменяют `TELEGRAM_TOKEN` и не дают боту доступ к пользовательскому аккаунту.

## Подготовка

```bash
git clone https://github.com/mazixs/Nuvio.git
cd Nuvio
mkdir -p .secrets
cp .env.example .secrets/.env
```

Заполните в `.secrets/.env`:

```env
TELEGRAM_TOKEN=1234567890:ABCdef...
ADMIN_IDS=123456789
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=0123456789abcdef0123456789abcdef
WEB_PASSWORD=replace-me
WEB_SECRET_KEY=replace-with-a-random-value
```

## Первый переход с облачного Bot API

Один токен бота нельзя одновременно использовать через облачный и локальный Bot
API. Поэтому переход выполняется вручную:

1. Остановите прежний экземпляр Nuvio.
2. Отвяжите токен от облачного Bot API:

   ```bash
   curl "https://api.telegram.org/bot<TOKEN_БОТА>/logOut"
   ```

3. Запустите новый стек:

   ```bash
   docker compose --env-file .secrets/.env up -d
   ```

Команда `logOut` намеренно не встроена в контейнер: автоматический вызов мог бы
прервать другой работающий экземпляр бота.

## Сервисы

| Сервис | Назначение | Доступ с хоста |
|---|---|---|
| `telegram-bot-api` | Локальный Telegram Bot API в режиме `--local` | не публикуется |
| `bot` | Загрузка, обработка и отправка материалов | не публикуется |
| `web` | Аналитический дашборд | `${WEB_PORT:-8080}` |

`bot` и `telegram-bot-api` используют общий том `/app/media`. Бот передаёт
локальному API абсолютный путь к файлу, поэтому большой файл не загружается во
внешнее промежуточное хранилище.

## Запуск

Готовый образ Nuvio из GHCR:

```bash
docker compose --env-file .secrets/.env pull bot web
docker compose --env-file .secrets/.env up -d
```

Сборка Nuvio из текущих исходников:

```bash
docker compose \
  --env-file .secrets/.env \
  -f compose.yaml \
  -f compose.dev.yaml \
  up -d --build
```

Проверка состояния:

```bash
docker compose --env-file .secrets/.env ps
docker compose --env-file .secrets/.env logs -f bot telegram-bot-api
```

Дашборд доступен по адресу `http://<адрес-сервера>:<WEB_PORT>`.

## Обновление

Для готового образа:

```bash
docker compose --env-file .secrets/.env pull
docker compose --env-file .secrets/.env up -d
```

Для сборки из исходников:

```bash
git pull
docker compose \
  --env-file .secrets/.env \
  -f compose.yaml \
  -f compose.dev.yaml \
  up -d --build
```

## Хранение и очистка

| Том или каталог | Содержимое | Постоянное |
|---|---|---|
| `bot-data` | SQLite-базы аналитики и кэша `file_id` | да |
| `telegram-bot-api-data` | служебное состояние локального API | да |
| `shared-media` | скачанные и обработанные медиа | нет, очищаются |
| `./logs` | журналы приложения | да, с ротацией |
| `./.secrets` | окружение и cookies | да |

Nuvio не создаёт архив всех скачиваний. Медиа удаляются после отправки или
ошибки. При старте и корректной остановке также выполняется очистка временного
каталога. Том `shared-media` нужен лишь для обмена путём между двумя
контейнерами.

## Cookies

Необязательные Netscape-файлы можно положить в `.secrets/`:

```text
.secrets/www.youtube.com_cookies.txt
.secrets/www.instagram.com_cookies.txt
.secrets/www.tiktok.com_cookies.txt
```

Их также можно обновлять через `/admin`.

## Возврат к облачному Bot API

1. Остановите стек.
2. Вызовите `logOut` у локального API, пока контейнер ещё доступен:

   ```bash
   curl \
     "http://127.0.0.1:8081/bot<TOKEN_БОТА>/logOut"
   ```

   По умолчанию порт 8081 не опубликован. Для этой операции временно добавьте
   локальный проброс `127.0.0.1:8081:8081` или выполните запрос внутри сети
   Compose.

3. Запустите приложение без `TELEGRAM_LOCAL_MODE`; тогда оно использует
   `https://api.telegram.org` и лимит 50 МБ.

## Запуск без Docker

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
set -a
source .secrets/.env
set +a
python main.py
```

Такой запуск по умолчанию работает через облачный Bot API. Для локального API
вне Compose потребуется самостоятельно обеспечить общий путь к файлам и задать
`TELEGRAM_LOCAL_MODE`, `TELEGRAM_BOT_API_BASE_URL`,
`TELEGRAM_BOT_API_FILE_URL`, `TELEGRAM_MAX_FILE_SIZE_MB` и `TEMP_DIR`.
