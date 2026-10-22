# B10: воспроизводимость Docker и интеграция

Дата: 22.10.2026 (Москва). Статус: **заблокировано**,
[PR #82](https://github.com/sb1nala/alfa-hack/pull/82) **фактически слит** в main `4b0d556`.
Слияние не снимает блокеры приёмки. Исторические результаты ниже относятся к
исходному проверенному commit `6efad96f822b236f0993a76da7e0caaa925ccfdd`;
новые результаты CI вынесены в [B13-REPORT.md](B13-REPORT.md).

## Проверенный код и границы

- `git fetch origin main`: актуальный main — `4d77082711a75694c0d99f7db860135f44551341`;
  после указанного в задании `4d77082` новых коммитов не было.
- Ветка `feat/backend-docker-e2e` создана от этого main, рабочее дерево до проверки
  было без изменений. Изменения B10 — только scripts/ и технические документы backend/.
  Код продукта, публичный API, старые миграции и зависимости не менялись.
- Проверенный код B10 — `6efad96f822b236f0993a76da7e0caaa925ccfdd`;
  последующее изменение добавляет только ссылку на созданный PR и этот commit в отчёт.
  Полный pytest выполнен на итоговом backend-коде, совпадающем с main; скрипты B10
  отдельно проверены описанными ниже прогонами.
- Проверены состояния GitHub: #61, #66/#67, #68, #69, #78 и #79 слиты;
  открытых PR на момент начала не было. #76 и #77 открыты. #6 и #9 открыты;
  #8 уже закрыт после #61, но это не подтверждает сборку текущего main.
- B08/B09/B11/B12 переиспользованы. Риски, frontend, mocks, seed, ml,
  api-collection и продуктовые документы не редактировались. Сообщения участникам
  не отправлялись; Issues не закрывались.

## Среда и изоляция

Это облачный Linux-контейнер с Docker in Docker, **не физически новый ПК**.
Python 3.12.14, uv 0.12.23, Docker Engine 28.4.0, Compose v2.40.3,
PostgreSQL 16.15, Node 24.19.0, pnpm 11.25.0, Playwright 1.57.0,
Chromium 154.0.8037.57. Зависимости backend/mocks установлены по uv.lock,
frontend — `pnpm install --frozen-lockfile`. Проверены Python 3.12 и UID 10001 в образе.
Теги базовых образов заданы в репозитории; digest не закреплён, поэтому побайтовая
воспроизводимость образов между датами не заявляется.

Для каждого стенда использованы случайные project name, четыре свободных loopback-порта,
новые volumes, пароль БД и JWT-секрет. Для чистой попытки `.env` получен из
`.env.example`, заменены только секреты, порты, project name и точный Origin.
Старых контейнеров/данных проекта не было. Pytest использовал отдельную PostgreSQL
и случайную БД, удаляемую фикстурой; бизнес-стенд pytest не использовался.
Глобальный prune не выполнялся. Скрипты удаляют только собственные ресурсы;
проверка сохранности сначала выполняет down **без** удаления volumes.

Особенности среды отделены от дефектов репозитория:

1. Первая штатная попытка упёрлась в недоверенный сертификат HTTPS inspection proxy
   внутри build. Это ограничение среды; TLS не отключался. Backend/mocks поддерживают
   `proxy_ca`, но штатный Compose его не передаёт; frontend Dockerfile поддержки CA
   не имеет. В отдельном локальном Compose-файле backend/migrate/worker/mocks получили
   `build.secrets: [proxy_ca]`, источник — системный combined CA bundle.
   Для frontend использован `dockerfile_inline` с исходным Dockerfile, где **только**
   установка зависимостей заменена на:

   ```dockerfile
   RUN --mount=type=secret,id=proxy_ca NODE_USE_ENV_PROXY=1 \
       NODE_EXTRA_CA_CERTS=/run/secrets/proxy_ca pnpm install --frozen-lockfile
   ```

   `RUN pnpm build`, исходные API-типы, lock-файл и api:check остались неизменны.
   CA передан BuildKit secret, не скопирован в образ; прокси сохранён.
   После этой настройки штатная сборка дошла до ошибки #76.
2. Облачный checkout выдаёт файлы mode 0600. Перед повтором восстановлены обычные
   режимы Git checkout (0644; executable — по индексу), без изменения байтов/индекса.
   Иначе UID 10001 не читает bind-mounted JSON, nginx — конфигурацию/статику.
3. Диагностический B09-стенд использует читаемые временные копии seed и frontend,
   CA через secret, обход внешнего прокси для loopback wget. Только явный
   `--allow-stale-types` регенерирует типы в этой копии. Это **не** чистый запуск.

## Команды

Из корня, env-файл — локальный, права 0600; секреты не выводить:

```bash
git fetch origin main
git switch -c feat/backend-docker-e2e origin/main
docker compose --env-file /path/to/b10.env up --build --wait
# Только для HTTPS inspection: отдельная описанная выше настройка CA среды
docker compose --env-file /path/to/b10.env -f docker-compose.yml \
  -f /path/to/proxy-only.json up --build --wait
```

Обе попытки завершились ошибкой: первая — CA, вторая — #76. Вторая не
регенерирует типы и воспроизводит дефект исходного frontend; настройки CA не
считаются исправлением репозитория. Успешного времени до готового чистого SPA нет.

Строгая повторная проверка после исправления frontend, без тестовых override:

```bash
PLAYWRIGHT_MODULE=/path/to/browser-tools/node_modules/playwright \
CHROMIUM_EXECUTABLE=/usr/bin/chromium python3 scripts/check_product_docker.py
```

Скрипт запускает `docker compose up --build --wait --wait-timeout 120`, не меняет
исходники/типы, не отключает api:check и не продолжает продуктовый прогон при ошибке
сборки. Полностью успешный прогон этого строгого пути пока **не подтверждён**.
Проверен также фактический запуск нового оркестратора: без настройки прокси Node
останавливается при загрузке pnpm из registry.npmjs.org (`EAI_AGAIN`), сценарии API
не запускаются, выполняется очистка собственного проекта. Это отдельное ограничение
среды; после описанной настройки прокси/CA сборка исходных типов всё равно падает #76.

Независимые проверки из backend/:

```bash
uv sync --locked
uv run --locked --env-file /path/to/b10.env pytest -q --run-postgres
uv run --locked ruff check . ../scripts --extend-exclude app/decision
uv run --locked ruff format --check . ../scripts --extend-exclude app/decision
uv run --locked python -m app.export_openapi --check
DOCKER_BUILD_CA=/etc/ssl/certs/ca-certificates.crt \
  uv run --locked python ../scripts/check_docker.py

# Только диагностический стенд с обходом #76; НЕ чистый checkout build:
PLAYWRIGHT_MODULE=/path/to/browser-tools/node_modules/playwright \
CHROMIUM_EXECUTABLE=/usr/bin/chromium \
DOCKER_BUILD_CA=/etc/ssl/certs/ca-certificates.crt \
  uv run --locked python ../scripts/check_sse_docker.py --allow-stale-types --product-checks
```

Внутри продуктового прогона явно заданы `E2E_BASE_URL=E2E_ORIGIN=http://127.0.0.1:<frontend-port>`
и `E2E_MOCKS_URL=http://127.0.0.1:<mocks-port>`; все пользовательские HTTP-запросы идут
через nginx. Прямой backend — только диагностика health и глубокий технический smoke.

## Фактическое покрытие существующих проверок

| Проверка | Что подтверждает | Что не подтверждает |
| --- | --- | --- |
| check_docker.py | Образы backend/mocks, Alembic upgrade/check, двойной seed, auth/cookie/CSRF, submit/idempotency/cancel, аудит, все seed-исходы настоящего decide(), два worker, retry/lease и сбои БД | Сборку frontend, UI и пользовательский HTTP через nginx; миграции/seed выполняет явно до API |
| check_sse_docker.py + check_sse_browser.cjs | Настоящий Chromium EventSource, cookie, nginx, heartbeat, Last-Event-ID, replay, restart/recreate | При --allow-stale-types не подтверждает чистую сборку; тестовый EventSource не означает SSE в React |
| api-collection/run_e2e.py | 9 компаний, 5 исходов, 3 вида контрпредложения, accept/sign/issue, отказ, исправление выручки, одно решение аналитика, негативные HTTP, наличие конечного статуса в истории | Нет upload/download файла, проверки его байтов, сохранности после пересоздания; история проверяется слабее полного порядка |

`run_e2e.py` опционально сбрасывает счётчик БКИ и проглатывает ошибку reset; ожидание
CHECKING/длительности само по себе не доказывает ровно два 503. Это отдельно подтвердил
глубокий smoke по двум повторным событиям/ревизиям. Проверка 404 в продуктовом runner
использует случайный UUID, а не существующую чужую заявку; права покрывает PostgreSQL pytest.
Название «сквозной» не используется как доказательство отсутствующих проверок.

## Сценарии: ожидание и факт

Следующие успехи относятся к **диагностическому** стенду с временной регенерацией
frontend-типов. В репозитории frontend не исправлен.

| Сценарий | Ожидание | Факт |
| --- | --- | --- |
| Исходный checkout, штатный up/build/wait | Все образы, migrate=0, готовый SPA | **FAIL #76**, Generated types are not up-to-date; готовность продукта не достигнута |
| Диагностический up/wait | db/backend/mocks/frontend healthy, migrate=0, worker running | PASS, миграции и seed автоматически, ручной подготовки БД нет |
| demo-approve | APPROVED → принятие → SIGNED → ISSUED | PASS |
| demo-term | COUNTER_OFFER, term_extended → ISSUED | PASS |
| demo-amount | COUNTER_OFFER, amount_reduced → отказ → CANCELLED | PASS |
| demo-product | COUNTER_OFFER, product_changed + collateral_required → ISSUED | PASS |
| demo-info | NEEDS_INFO → выручка 9,4 млн → APPROVED → ISSUED | PASS |
| demo-manual | MANUAL_REVIEW → analyst COUNTER_OFFER → ISSUED | PASS |
| demo-stop / demo-risk | REJECTED с company_liquidating / high_risk | PASS |
| demo-bki | Два 503, автоматические повторы → APPROVED → ISSUED | PASS; продуктовый исход за 11,2 с, ровно два повтора отдельно в глубоком smoke |
| Негативные запросы через nginx | 401/403 роль/422 ИНН/403 CSRF/403 Origin/409 revision/404/logout | PASS, итог runner **10/10** |
| PDF + дополнительные данные | NEEDS_INFO, upload=201, точные байты download, повторная обработка → ISSUED | PASS |
| Аудит дополнительного сценария | DRAFT application_updated, далее точный порядок status_changed до ISSUED, document_uploaded | PASS |
| Down/up с прежними томами | Та же карточка/решение/оффер, история, PDF, сессия; нет новых строк | PASS, полное равенство карточки/истории/метаданных/байтов, стабильные количества строк |
| Повторный migrate/seed | Нет повторных аккаунтов/компаний/продуктов/решений | PASS, unchanged=10/9/4, количества бизнес-строк не изменились |
| Worker SIGKILL во время HTTP | Lease recovery, одно решение; отменённая заявка не получает поздний ответ | PASS check_docker.py |
| PostgreSQL pause/stop/restart | ready=503, live=200, возврат без ручного рестарта worker | PASS check_docker.py, новая заявка после восстановления обработана; дополнительно через nginx сохранены карточка/история/PDF после сбоя |
| Полный backend pytest с PostgreSQL | Все актуальные тесты | **549 passed, 1 warning, 242,62 с**, без skipped; прежнее предупреждение Starlette/TestClient/httpx |
| Ruff / format / OpenAPI / diff | Без ошибок в своей области | PASS, 71 Python-файл format check вне app/decision |

## Сервер SSE, тестовый браузер и рабочий UI

Серверные кадры/авторизация/ресурсы подтверждены полным pytest и B09-тестами с
реальным Uvicorn/TCP/PostgreSQL. Браузерный прогон и рабочий UI фиксируются раздельно:

- Браузерный EventSource: **PASS**, первый кадр 90 мс, heartbeat 15002 мс,
  `text/event-stream; charset=utf-8`, Cache-Control no-cache. Login → cookie →
  submit → SUBMITTED/CHECKING/SCORING/APPROVED → карточка. Native Last-Event-ID после
  restart, onopen/refetch, recreate backend через nginx, восстановление CLOSED,
  продолжение доставки CANCELLED и replay сохранённого события — PASS.
- Рабочий UI: в исходном `frontend/src/api/hooks.ts` — refetchInterval 2/5 с,
  в bank.ts — 10 с. Исполняемого `new EventSource` в frontend нет; упоминания
  в schema.d.ts — только документация. Поэтому **SSE в продукте не реализован**.
- Добавлен отдельный smoke рабочего SPA через реальные формы/кнопки без injected API
  и без reload; он сообщает количество запросов /events отдельно от тестового SSE.
  **PASS:** login → подбор → ИНН → submit → решение → accept → sign → ISSUED;
  один document request (без перезагрузки), **0 запросов EventSource, 8 GET карточки**.
  Рабочий UI обновляется polling. Отдельный UI-прогон завершился с кодом 0.

При разработке дополнительной проверки исправлены ожидания тестового кода:
DRAFT создаётся как application_updated (не status_changed), а MUI дополняет
доступные названия обязательных login/password полей звёздочкой. Продуктовая логика
и ожидаемые кредитные исходы не подменялись. После исправления аудита проверки
API/документа/сохранности/сбоя БД и EventSource прошли; UI повторён отдельно, без
повтора дорогого lease/fencing-прогона.
Ввод суммы/выручки в UI также приведён к целым рублям: API/seed хранят `.00`,
а поле интерфейса принимает целые рубли. Отдельный повтор выполняется командой
`check_sse_docker.py --allow-stale-types --ui-only` с теми же browser/CA переменными.

## Блокеры и передача владельцам

1. **Frontend, [#76](https://github.com/sb1nala/alfa-hack/issues/76), P0:**
   `cd frontend && pnpm api:generate && pnpm typecheck && pnpm build`;
   закоммитить schema.d.ts и необходимые изменения кода. api:check сохраняется.
   Причина — docs/openapi.yaml изменён B09/#69 после генерации типов B12.
2. **Frontend, [#6](https://github.com/sb1nala/alfa-hack/issues/6):** подключить
   EventSource с HttpOnly cookie, именованные события, onopen/refetch,
   обработку revision/id, историю и восстановление CLOSED после 502/recreate.
   Нужна приёмка рабочего интерфейса; тестовый JS EventSource это не заменяет.
3. **Backend/B13, [#77](https://github.com/sb1nala/alfa-hack/issues/77):**
   взять за основу QA-ветку `qa/e2e-stand`, добавить PR-проверки чистой сборки,
   Ruff/format/OpenAPI/PostgreSQL pytest, продуктовый и браузерный прогон,
   сохранение безопасных артефактов. Branch protection — отдельная настройка владельца.

Продукту: TS-10 ожидает decision_created, но штатный B08 сигнализирует итог через
status_changed; подписчик должен перечитывать карточку на всех предусмотренных
событиях. Публичная история не содержит внутреннего комментария аналитика:
для него используется банковская карточка. Документы продукта не переписывались.

После frontend-исправлений нужен повтор строгого исходного Compose-запуска и проверка
SSE в UI, затем B10 можно переводить «На ревью». До этого факт слияния #82 не закрывает B10,
#6/#76/#77. Полноценный CI в B10 не добавлен. Секреты, JWT/cookies и сырые чувствительные
логи в этот отчёт не включены.
