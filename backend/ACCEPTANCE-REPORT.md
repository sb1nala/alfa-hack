# Техническая приёмка backend после B13

Снимок: **23.10.2026, UTC**. Проверен свежий `origin/main`:
[`1c93ac7c32e644667e99959109b90a04cf6403aa`](https://github.com/sb1nala/alfa-hack/commit/1c93ac7c32e644667e99959109b90a04cf6403aa).
Изолированная ветка документации создана именно от него. Изменения кода продукта,
frontend, рисков и общих продуктовых документов в эту приёмку не входят.

**Backend технически готов в проверенном объёме; блокер сдачи backend не выявлен.**
B10 и реализация B13 завершены и слиты. Общая QA-приёмка остаётся открытой:
восстановление рабочего React UI после потери сети/CLOSED/502 этим отчётом не принято.
Required checks должен включить тимлид.

## Слияния и доказательства на зафиксированном main

GitHub PR metadata и `git merge-base --is-ancestor` подтвердили включение:

| Работа | PR, состояние | Merge commit |
| --- | --- | --- |
| B10 | [#82](https://github.com/sb1nala/alfa-hack/pull/82), merged 22.10 | `4b0d556941690bda9351f85c7fa79c14f24c0e73` |
| B13 | [#84](https://github.com/sb1nala/alfa-hack/pull/84), merged 23.10 | `2003f5ad6f71fb87126d42d6659678ed8b1a45ad` |
| Frontend SSE | [#85](https://github.com/sb1nala/alfa-hack/pull/85), merged 22.10 | `d1de96c49dde410f25597e36b119c2d362fd552c` |
| API review_reason, Issue #88 | [#89](https://github.com/sb1nala/alfa-hack/pull/89), merged 23.10 | `1c93ac7c32e644667e99959109b90a04cf6403aa` |

Основное доказательство — [Project CI #15, run 39926680775](https://github.com/sb1nala/alfa-hack/actions/runs/39926680775),
`event=push`, `head_branch=main`, `head_sha=1c93ac7…`, attempt 1, **success**.
Проверены metadata jobs, шаги и фактические логи checkout/проверок:

- [Backend checks — success](https://github.com/sb1nala/alfa-hack/actions/runs/39926680775/job/120324682397):
  **576 passed, 1 warning, 202,81 с**; JUnit gate — 576, без skips/failures/errors.
  Ruff, format вне `app/decision`, OpenAPI `--check`, migration roundtrips и очистка прошли.
- [Clean Docker product — success](https://github.com/sb1nala/alfa-hack/actions/runs/39926680775/job/120324682864):
  исходный Compose build/up, API **10/10**, persistence, серверный SSE и рабочий UI smoke;
  адресная очистка прошла. Успешные jobs не загружают failure artifacts — доказательство в логах.

Обозначения ниже: **B** — backend job выше; **D** — Docker product job выше;
**L** — отдельная локальная проверка отказов ниже. Повтор полного pytest/Compose/browser
не выполнялся: текущий main уже имеет нужный успешный CI. Старые результаты
`16525ca`, `1b3732d`, `aa83e9c` и PR-head `e73eeaf` остаются историческими,
не подменяют проверку merge commit. История B10/B13 сохранена в исходных отчётах.

## Матрица технических критериев

| Критерий | Доказательство и фактический результат | Итог / граница |
| --- | --- | --- |
| Штатный Compose build/up | D: `check_product_docker.py` → исходный `up --build --wait`; frontend выполняет `pnpm api:check && pnpm typecheck && vite build` | PASS; без регенерации типов, overrides и обхода api:check |
| Миграции и seed через migrate | D: автоматический `migrate`, запуск API/worker после его успеха; повтор `run --rm migrate`, неизменные количества строк; B: upgrade/repeat/check/downgrade с сохранением старых данных | PASS; ручная подготовка продуктовой БД не нужна |
| Настоящие API/решения рисков | D: неизменённый `run_e2e.py` через nginx, все 9 компаний, 5 исходов, причины и 3 контрпредложения; B: `tests/decision` и worker с настоящим decide() | PASS; кредитная политика не переутверждалась |
| Предложения, данные, документы, ручные решения | D: accept/sign/ISSUED, отказ/CANCELLED, NEEDS_INFO → повтор, PDF upload/download с точными байтами, аналитик → COUNTER_OFFER; B: остальные ручные исходы, конфликты и файловые ограничения | PASS; UI всех вариантов не принимался |
| Аудит и доступ | D: точный порядок истории до ISSUED, document_uploaded, негативные HTTP; B: чужие существующие заявки/документы, обе роли, CSRF/Origin, устаревшая revision, откаты аудита, банковская/публичная изоляция | PASS; случайный UUID из API runner сам по себе не доказывает права на чужую заявку |
| БД и файлы после пересоздания | D: настоящий down/up с теми же volumes; равенство карточки/предложения, истории, PDF/метаданных и сессии; неизменные количества бизнес-строк | PASS; не проверка резервного копирования |
| Временный отказ БД/мока | D: ready=503/live=200, возврат API и прежних данных; B: retry timeout/503/429/network, supervisor, lease/fencing; L: реальная остановка БД во время CHECKING и процесса mocks | PASS; те же API/worker восстанавливают обработку без requeue/restart |
| Серверные SSE / Last-Event-ID / heartbeat | D: Chromium через nginx, heartbeat **15002 мс**, restart/recreate backend, Docker DNS, native Last-Event-ID, refetch и сохранённый replay | PASS; отдельный тестовый EventSource |
| Освобождение SSE-ресурсов | B: `test_events_postgres.py`: disconnect/отмена/shutdown, DB/send timeout, закрытие генератора, pool.checkedout=0 и pg_stat_activity, backpressure/ограниченные пачки | PASS; реальный Uvicorn/TCP/PostgreSQL, не буферизующий ASGITransport |
| OpenAPI / frontend-типы / review_reason | B: экспорт `--check`, контракт, снимки причин и клиентская изоляция; D: api:check/typecheck/build, включая schema.d.ts из #89 | PASS в main; поле nullable и только банковское, неизвестный код допустим |
| Рабочий React UI, основной путь | D: реальные формы до ISSUED без reload, **1 EventSource / 11 GET карточки**, успешный text/event-stream | PASS smoke; GET при действиях/инвалидациях не означает polling |
| React UI после потери сети/CLOSED/502 | D разрывает соединение у отдельного тестового EventSource; рабочий React smoke не отключает сеть/API | **НЕ ПРОВЕРЕНО** на полном стенде; frontend/QA |

## Дополнение L: адресный прогон отказов на том же commit

Облачный Linux, Python **3.12.14**, `uv sync --locked`, PostgreSQL **16.15**
(`postgres:16-bookworm`, digest `sha256:d81f7b51697014367236d07ec58b54153fc28f828b72bf247fe5c7487520bcb0`).
Одна новая БД в контейнере `alfa-final-<random>`, случайные loopback-порты/секреты,
временный DOCUMENTS_DIR. API, worker и неизменённый мок — отдельные локальные процессы
из checkout, зависимости backend по lock. Это проверка обработки отказов через прямой
API, **не повтор штатной Compose-сборки/nginx**. Defaults timeout/retry/lease сохранены.

Воспроизведение (только на своём стенде):

1. Применить `alembic upgrade head`, импортировать штатные accounts/companies/products/texts;
   запустить `uvicorn app.main:app`, `python -m app.worker` и `uvicorn mocks.app.main:app`
   (для мока `MOCKS_SEED_PATH=seed/companies.json`, `MOCKS_LATENCY_MS=500`). Войти тестовым
   клиентом и создать черновик из первого сценария seed, `demo-approve`.
2. Остановить только процесс мока; отправить черновик. Дождаться
   `pipeline_jobs.state=pending, last_error_category=network`. Проверить CHECKING,
   отсутствие решения и доступность карточки/сессии. Запустить мок на прежнем порту.
   **Факт: APPROVED, completed, ровно одно решение и предложение; API/worker не перезапускались.**
3. Отправить вторую заявку, дождаться CHECKING, остановить только свой PostgreSQL.
   Проверить ready=503/live=200 и наблюдаемую `category=database_or_internal` supervisor.
   Запустить ту же БД. **Факт: прежние API/worker живы, та же заявка достигла APPROVED
   в пределах стандартного lease и 90-секундного deadline; одно решение/предложение,
   completed, без ручного requeue.**
4. Остановить только созданные процессы и удалить точный контейнер с anonymous volume.
   **Факт: очистка подтверждена, чужие контейнеры/volumes/данные не использовались.**

Локальный временный harness `/tmp/alfa-final-acceptance/check_recovery.py`, SHA256
`fd4a16a514140b56d02c1e62a8c5b3b17d01939d4477fe694a8fb5fb58199736`;
безопасный итог `/tmp/alfa-final-acceptance/result.txt`: оба сценария и очистка PASS.
Это локальное свидетельство, не GitHub CI/job. Сырые логи процессов удалены с временным
каталогом. Первая попытка harness остановилась до сценариев из-за временного init-сервера
PostgreSQL; ожидание заменено на успешный TCP SQL. Дефект продукта этим не исправлялся.

## Оставшиеся зависимости и рекомендации по Issues

Состояния прочитаны в GitHub 23.10: #6/#9/#77 открыты; #88 уже закрыт через #89.
Issues автоматически не закрывались, сообщения участникам не отправлялись.

| Issue | Рекомендация |
| --- | --- |
| [#6](https://github.com/sb1nala/alfa-hack/issues/6) | Сервер завершён, SSE рабочего UI в основном сценарии подтверждён D. Для итоговой QA-приёмки оставить открытым до проверки настоящего React на полном стенде: offline → возврат, CLOSED/502 при recreate, догон карточки/всех страниц истории, отсутствие дублей/отката revision, уход/logout/конечный статус освобождают поток. Затем можно закрыть; отдельный JS EventSource за это не засчитывать. |
| [#9](https://github.com/sb1nala/alfa-hack/issues/9) | Содержательно переходы/запреты/аудит проверены B, 576 тестов без skips. Но буквальный DoD `docker compose run --rm backend pytest` расходится с runtime Dockerfile: dev-зависимости и tests в образ не входят (адресная проверка образа ниже). Сначала владельцу согласовать актуальную команду полного pytest с PostgreSQL из backend/README либо отдельную test-target задачу; до этого безусловное закрытие не рекомендовано. |
| [#77](https://github.com/sb1nala/alfa-hack/issues/77) | CI реализован и слит, оба job успешны на нужном main. Осталось включить и подтвердить required checks у тимлида; после этого можно закрыть. Наличие workflow само по себе обязательность не доказывает. |
| [#88](https://github.com/sb1nala/alfa-hack/issues/88) | Backend/API выполнен в слитом #89 и проверен B/D на main, закрытое состояние обосновано для API-задачи. Отдельная frontend-зависимость: подключить manualReviewReasons по review_reason, fallback на analytical_explanation при null/неизвестном коде. В текущем BankApplicationPage пока используется analytical_explanation; это статическая проверка, не новый браузерный прогон. |

Frontend/QA: кроме resilience выше, принять отображение review_reason и оставшиеся
экранные критерии TS-01…TS-13; backend API-проверки не являются полной UI-приёмкой.
Результаты #85 на синтетическом API — историческое свидетельство автора frontend,
не проверка восстановления React на текущем полном Docker-стенде.

## Required checks: что доступно и что сделать тимлиду

Read-only GitHub API 23.10:

- `GET /repos/sb1nala/alfa-hack/branches/main`: **protected=false**,
  `required_status_checks.enforcement_level=off`, `contexts=[]`, `checks=[]`.
- `GET /repos/sb1nala/alfa-hack/rulesets`: **200, []**.
- `GET /repos/sb1nala/alfa-hack/branches/main/protection`: **403 Resource not accessible by integration**.
  Полные настройки защиты этой интеграции недоступны. Доступные сведения показывают,
  что обязательность checks не включена; полную конфигурацию должен подтвердить владелец.

Тимлиду: Settings → Rules → Rulesets (либо Branches → protection rule для `main`),
включить правило, требовать PR и status checks перед merge; выбрать **оба точных имени**:
**Backend checks**, **Clean Docker product** (GitHub Actions). Сохранить, проверить
применение к `main` и что PR без успешных обоих checks нельзя слить при действующих
правилах/bypass. В этой работе настройки не менялись; merge отчётного PR не выполняется.

## Замечания, не блокирующие backend

| Замечание / серьёзность / владелец | Воспроизведение, ожидаемое и фактическое |
| --- | --- |
| DoD #9, P2, backend/тимлид | Собрать `backend/Dockerfile`, выполнить `docker run --rm <образ> pytest`: ожидается зелёный pytest по Issue; фактически **exit 127**, executable not found. Проверка Python внутри того же образа: модуль pytest отсутствует, `/app/tests` отсутствует. B запускает полный pytest из checkout и проходит. Это несогласованность способа проверки, не сбой обработки заявок. |
| Устаревшие продуктовые статусы, P2, продукт/QA | README → строка «Live-статус по SSE»: ожидается отражение слитого #85, фактически указано polling/TODO. TS-10 ожидает decision_created, тогда как автоматический B08 сигнализирует решением через status_changed; ожидается описание всех допустимых сигналов с refetch. Не менять backend ради устаревшего текста; владельцу обновить документы. |
| Причина ручной проверки в UI, P2, frontend | Для MANUAL_REVIEW API теперь содержит review_reason; ожидается выбор словаря по #88, фактически BankApplicationPage:93–95 по коду показывает analytical_explanation. Backend не блокирует подключение. Браузерный результат отображения не заявляется. |

Новых воспроизведённых дефектов продукта в backend в проверенном объёме не найдено.
Непокрытый UI resilience — зависимость QA, а не доказанный дефект. Историческая отмена
CI `12fe47a` и её неизвестная первопричина не объявляются исправленным продуктовым багом.

Адресная проверка runtime #9 выполнена локально из этого checkout: `docker build
--secret id=proxy_ca,src=/etc/ssl/certs/ca-certificates.crt -t <свой-образ> backend`,
затем команда из таблицы. CA передан только временным BuildKit secret, TLS не отключался;
исходный Dockerfile не менялся. Это не дополнительная приёмка чистого Compose.
Оба проверочных контейнера и собственный тег образа удалены.
