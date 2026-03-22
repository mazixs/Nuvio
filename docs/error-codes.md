# Error Codes

Пользователям бот показывает только короткий код ошибки без внутренних деталей. Полная диагностика уходит в server logs.

Формат кода:

```text
<PREFIX>-<CATEGORY>-<RANDOM>
```

Примеры:

- `YT-ACCESS-A1B2C3`
- `IG-RATE_LI-Z9X8Y7`
- `TG-NETWORK-Q1W2E3`

## Prefixes

- `YT` - YouTube extractor, metadata, download or merge pipeline
- `TT` - TikTok extractor or download pipeline
- `IG` - Instagram extractor or download pipeline
- `TG` - Telegram API, delivery or network path
- `FILE` - local filesystem, temp files, permissions, missing file
- `BOT` - internal bot orchestration, callback handling, generic runtime flow

## Categories

Категория сокращается до 8 символов в самом коде, поэтому в логе и в коде может использоваться укороченный вид.

- `ACCESS` - ошибка доступа, чаще всего cookies, restricted content или права на файл
- `API` - ошибка Telegram API или другого внутреннего API-слоя
- `CALLBACK` - ошибка callback/inline interaction
- `DATA` - битые или неполные данные от extractor
- `EXTRACTO` - сбой extractor/runtime logic
- `FFMPEG_M` - на сервере отсутствует FFmpeg или merge pipeline не может его использовать
- `FORMAT_U` - запрошенный формат недоступен или устарел
- `LARGE` - файл превышает допустимый лимит для выбранного сценария
- `NETWORK` - временная сетевая ошибка
- `RATE_LI` - rate-limit со стороны платформы
- `SEND` - ошибка финальной отправки файла пользователю
- `TIMEOUT` - истечение таймаута
- `UNKNOWN` - неклассифицированная ошибка

## How To Read In Production

1. Пользователь присылает вам короткий код ошибки.
2. Ищите этот код в `journalctl` или в вашем log sink.
3. В log entry смотрите:
   - platform
   - stage
   - session_id
   - url
   - cookie_health_status
   - cookie_health_summary
   - traceback

Пример поиска в systemd:

```bash
journalctl -u nuvio.service -n 500 --no-pager | grep "YT-ACCESS-A1B2C3"
```

## Notes

- Протухшие или битые cookies должны проявляться в логах через `cookie_health_status`, а пользователю показывается только код.
- Не передавайте пользователям traceback, текст extractor errors, пути к файлам, имена модулей или структуру сервера.
- Если ошибка массовая и повторяется по многим кодам одной категории, сначала проверяйте `yt-dlp`, cookies и сеть сервера.
