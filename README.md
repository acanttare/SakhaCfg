# SakhaNet - Smart Xray Config Checker

Большой автоматизированный пайплайн для проверки прокси-конфигов (VLESS/VMESS/Trojan/SS) через Xray в GitHub Actions.

Проект:
- собирает подписки из `sources.txt`,
- извлекает конфиги,
- проверяет их параллельно через локальный SOCKS-инстанс Xray,
- отбирает лучшие по latency,
- добавляет страну и флаг,
- публикует результаты в `output/`,
- сохраняет артефакты и (при изменениях) делает commit в репозиторий.

---

## Why This Project

Большинство публичных подписок:
- содержат дубли,
- часто включают нерабочие или нестабильные ноды,
- могут иметь плохие теги и названия,
- иногда ломаются на edge-кейсах парсинга.

SakhaCfg решает это автоматической валидацией и регулярным обновлением списка реально рабочих конфигов.

---

## Key Features

- **Параллельная проверка**: многопоточная обработка сотен конфигов.
- **Xray runtime check**: фактическая проверка через `socks5h://127.0.0.1:<port>`.
- **Гибкие тестовые URL**: fallback по нескольким endpoint'ам.
- **Устойчивость к сетевым сбоям**: retry на URL и обработка распространенных исключений.
- **Топ лучших конфигов**: отбор fastest нод с ограничением количества.
- **Гео-маркировка**: страна + флаг в имени (`vless(🇩🇪 DE)`, `vmess(🇺🇸 US)`).
- **Subscription metadata**: title/limit/expiry в результатах.
- **Артефакты CI**: `working.txt`, `working.json`, `failed.json`.
- **Автокоммит результатов**: коммитит только если есть изменения.
- **Smoke test перед запуском**: быстрый контроль синтаксиса Python-скриптов.

---

## Repository Structure

```text
.
|- .github/workflows/check-configs.yml   # основной workflow
|- scripts/
|  |- main.py                            # orchestrator, сбор output
|  |- fetcher.py                         # загрузка подписок и extraction конфигов
|  |- parser.py                          # парсинг URI -> outbound Xray
|  |- checker.py                         # проверка через Xray + latency
|  |- requirements.txt                   # Python зависимости
|- sources.txt                           # список URL подписок
|- output/
|  |- working.txt                        # финальная подписка (лучшие конфиги)
|  |- working.json                       # расширенная статистика
|  |- failed.json                        # причины fail по конфигам
```

---

## How It Works (Pipeline)

1. **Load sources**
   - читается `sources.txt`,
   - игнорируются пустые строки и комментарии (`#`).

2. **Fetch and decode**
   - для каждого URL делается `GET`,
   - при необходимости контент декодируется из base64.

3. **Extract configs**
   - регуляркой вытягиваются URI схем:
     - `vless://`
     - `vmess://`
     - `trojan://`
     - `ss://`
   - выполняется дедупликация.

4. **Check each config**
   - URI парсится в Xray outbound,
   - Xray поднимается локально на отдельном SOCKS-порте,
   - выполняется HTTP-тест через прокси,
   - снимается latency.

5. **Rank and trim**
   - рабочие конфиги сортируются по `latency_ms`,
   - применяется лимит (`MAX_WORKING_CONFIGS`, сейчас 50).

6. **Geo label**
   - определяется страна по host,
   - формируется имя в формате `protocol(FLAG CODE)`.

7. **Publish output**
   - `output/working.txt` (конфиги для использования),
   - `output/working.json` (полная статистика),
   - `output/failed.json` (диагностика).

---

## Output Format

### `output/working.txt`

В начале файла идет metadata-блок подписки:

```text
# title: SakhaCfg Subscription
# expires_at: 2026-12-31
# limit_gb: 100
```

Далее - список лучших рабочих конфигов (до 50 строк).

### `output/working.json`

Содержит:
- информацию по запуску (`updated_at`, runner),
- сводные счетчики,
- метаданные подписки,
- список рабочих,
- список нерабочих.

### `output/failed.json`

Содержит массив неуспешных конфигов и причину ошибки для каждого.

---

## GitHub Actions Workflow

Workflow: `.github/workflows/check-configs.yml`

Что делает:
- поднимает Python 3.12,
- ставит зависимости,
- выполняет smoke-test Python файлов,
- кэширует и скачивает Xray,
- запускает checker (с continue-on-error),
- всегда загружает артефакты,
- при изменениях коммитит новые результаты.

### Trigger'ы

- `workflow_dispatch`
- `schedule` (каждые 6 часов)
- `push` по изменениям `sources.txt`

---

## Configuration (Environment Variables)

Ниже все ключевые настройки, которые можно менять в workflow.

### Core

- `XRAY_BIN` - путь к бинарю Xray.
- `PARALLEL_WORKERS` - количество параллельных потоков проверки.
- `BASE_PORT` - базовый SOCKS-порт для пула.

### Network & Timeouts

- `TEST_TIMEOUT` - timeout HTTP-проверки (сек).
- `TCP_TIMEOUT` - timeout TCP-предпроверки.
- `XRAY_START_WAIT` - ожидание старта Xray.
- `TEST_URLS` - список URL через запятую для fallback-проверки.
- `RETRY_PER_URL` - число повторов на каждый test URL.

### Validation

- `XRAY_VALIDATE_CONFIG`
  - `0`: быстрее, без `xray run -test`,
  - `1`: медленнее, но строже по валидности конфига.

### Subscription Metadata

- `SUBSCRIPTION_TITLE` - заголовок подписки.
- `SUBSCRIPTION_EXPIRES_AT` - дата окончания (например `2026-12-31`).
- `SUBSCRIPTION_LIMIT_GB` - лимит трафика.
- `MAX_WORKING_CONFIGS` - максимальное число лучших конфигов.

### Geo

- `GEO_URL` - API endpoint геолокации (шаблон с `{host}`).
- `GEO_TIMEOUT` - timeout геозапроса.

---

## Performance Notes

Если у вас 400+ конфигов и хотите еще быстрее:

1. Поднимите `PARALLEL_WORKERS` (например 20 -> 24/28).
2. Снизьте `TEST_TIMEOUT` (10 -> 8).
3. Поставьте `RETRY_PER_URL=0` для максимальной скорости.
4. Оставьте `XRAY_VALIDATE_CONFIG=0` (не запускать двойной check).
5. Следите за rate-limit гео API; при необходимости кешируйте/меняйте провайдера.

Рекомендуемый баланс speed/stability:
- `PARALLEL_WORKERS=20`
- `TEST_TIMEOUT=10`
- `RETRY_PER_URL=1`
- `XRAY_VALIDATE_CONFIG=0`

---

## Troubleshooting

### `xray start timeout`

Причины:
- слишком высокий параллелизм,
- слабый runner,
- долго стартует процесс.

Что делать:
- увеличить `XRAY_START_WAIT`,
- снизить `PARALLEL_WORKERS`.

### `xray exited early`

Обычно:
- битый/неподдерживаемый конфиг,
- конфликт параметров stream/tls/reality.

Проверьте:
- сообщения в `failed.json`,
- корректность парсинга в `parser.py`.

### `Missing dependencies for SOCKS support`

Нужен `PySocks` в `scripts/requirements.txt`.

### `SOCKSHTTPSConnectionPool ... Read timed out / Connection reset`

Это часто сетевой шум ноды, а не баг кода.
Используйте:
- `TEST_URLS` с fallback,
- `RETRY_PER_URL=1`.

### `IndentationError` / `SyntaxError`

Их должен ловить шаг smoke test (`py_compile`) до запуска checker.

---

## Security & Operational Considerations

- Используйте только доверенные источники в `sources.txt`.
- Не храните приватные токены/секреты в репозитории.
- Понимайте юридические и policy-ограничения на использование прокси в вашей стране/организации.
- Следите за обновлениями Xray-core и совместимостью конфигов.

---

## Roadmap Ideas

- Балансировка отбора: latency + diversity по странам.
- Отдельные топ-листы по протоколам (`top-vless`, `top-vmess`, ...).
- Экспорт в дополнительные форматы.
- Умный blacklist нестабильных хостов по истории падений.
- Метрики и дашборд (время прогона, success ratio, top countries).
- Unit-тесты для parser/checker.

---

## Quick Start

1. Заполните `sources.txt` ссылками на подписки.
2. Запустите workflow `Check configs` вручную (`workflow_dispatch`).
3. Дождитесь завершения.
4. Заберите артефакты или смотрите обновленные файлы в `output/`.

---

## Example `sources.txt`

```text
# one source per line
https://example.com/subscription-1
https://example.com/subscription-2
```

---

## License

MIT License

Copyright (c) 2026 acanttare

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

---

## Final Notes

SakhaCfg уже настроен как практичный production-like конвейер:
- стабильная проверка,
- адекватная скорость,
- диагностические артефакты,
- аккуратный финальный список лучших конфигов.

Если захотите, можно сделать еще более "премиальный" README v2:
- с бейджами,
- диаграммой пайплайна,
- таблицей всех ENV со значениями по умолчанию,
- GIF/скриншотами GitHub Actions summary,
- FAQ блоком для пользователей подписки.
