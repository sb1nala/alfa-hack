# Backend

Python 3.12, FastAPI, async SQLAlchemy 2/asyncpg, PostgreSQL 16, Alembic, uv.
B01–B08: авторизация, JWT-сессии, CSRF, роли, каталог продуктов, поиск компании через
локальный мок ЕГРЮЛ, черновики, submit/cancel, аудит и постоянные задания в PostgreSQL.
Один worker выполняет проверки B07 и настоящее решение B08; GET карточки возвращает результат.
Код B08 из feat/backend-decision (333cc6c) включён владельцем репозитория в main
через [PR #64](https://github.com/sb1nala/alfa-hack/pull/64).
Банковские операции, файлы, история API и SSE пока возвращают `501` после проверок доступа.
Контракт: `../docs/openapi.yaml`.

## Настройки авторизации

Создайте `.env` по `.env.example` и задайте собственные `POSTGRES_PASSWORD` и
`AUTH_JWT_SECRET`. У секрета нет встроенного значения; runtime API без него не
запускается. Сгенерировать криптографически случайное значение:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

- `AUTH_JWT_SECRET`: минимум 32 символа, для развёртывания используйте результат
  генерации выше. Храните вне Git/логов; смена секрета инвалидирует все JWT и CSRF.
- `AUTH_JWT_ISSUER=alfa-hack`, `AUTH_JWT_AUDIENCE=alfa-hack-web`:
  проверяются вместе с подписью HS256, обязательными claims, временем и состоянием БД.
- `AUTH_SESSION_HOURS=8`: срок сессии без refresh, настраивается от 1 до 168 часов.
- `AUTH_ALLOWED_ORIGIN`: точный origin фронтенда/API, с портом, без пути и `/` в конце.
  Например, `http://localhost:8000` для прямого локального API либо origin фронтенда
  с его `/api` прокси. Во всех мутациях Origin обязателен; Host и X-Forwarded-* не
  используются для его определения. Другой порт, поддомен или отсутствующий Origin — 403.
- `AUTH_COOKIE_SECURE=true` для HTTPS. Для локального HTTP явно задайте `false`
  (как в `.env.example`); несогласованная пара origin/Secure блокирует запуск API.
  `access_token` и `csrf_context`: HttpOnly, SameSite=Lax, Path=/, без Domain.

JWT содержит sub, role, sid, iat, exp, iss, aud. Сервер на каждом защищённом запросе
проверяет также пользователя, текущую роль, срок и отзыв сессии в PostgreSQL.
Токен и password_hash никогда не входят в JSON. Logout отзывает сессию в БД, поэтому
скопированный JWT тоже перестаёт работать. Стабильные настройки и та же БД сохраняют
сессии после рестарта. Регистрации, refresh, восстановления пароля и OAuth нет.
Все auth-ответы, включая ошибки, имеют `Cache-Control: no-store`.
Если cookie содержит недействительный JWT (например, после отзыва сессии или
смены секрета), защищённые запросы возвращают 401. Для повторного входа вызовите
`GET /api/auth/csrf`: он удалит недействительную access_token cookie и выдаст
анонимный CSRF-контекст. Затем выполните обычный login и обновите CSRF-токен.
Удалять HttpOnly cookies вручную не нужно.

## Миграции

`docker compose up -d --build` из корня репозитория (с `.env` по `.env.example`) поднимает готовый стенд сам: одноразовый сервис `migrate` применяет `alembic upgrade head` и идемпотентно импортирует аккаунты, компании и продукты из `seed/`, после чего завершается с кодом 0. `backend` и `worker` стартуют только после этого (`service_completed_successfully`). Повторный `up` безопасен: импорт пишет `unchanged`.

Вручную — те же шаги по отдельности:

```bash
docker compose up -d --wait db
docker compose build backend mocks
docker compose run --rm backend alembic upgrade head
docker compose run --rm backend alembic check
docker compose run --rm backend python -m app.seed   --accounts /seed/accounts.json --companies /seed/companies.json   --products /seed/products.json --product-texts /seed/product-texts.json
docker compose up -d --wait backend mocks
```

В Git Bash на Windows перед командами с путями `/seed/...` задайте `MSYS_NO_PATHCONV=1`, иначе Git Bash превратит их в пути Windows и импорт не найдёт файлы.

Образ содержит Alembic и миграции, работает от пользователя `app` (UID 10001).
Сам API миграции не запускает и `create_all` не вызывает — это делает сервис `migrate`. Healthchecks и Swagger
`http://localhost:8000/docs` сохранены.

Локально (PostgreSQL уже запущена):

```bash
cd backend
uv sync --locked
uv run --locked --env-file ../.env alembic upgrade head
uv run --locked --env-file ../.env alembic check
```

Для подключения используются только существующие `Settings` и `POSTGRES_*`.
В `.env` хоста `POSTGRES_HOST=localhost`, опубликованный `POSTGRES_PORT`; внутри
Compose — `db:5432`. URL собирается через SQLAlchemy `URL.create`, передаётся
движку как объект и не интерполируется в `alembic.ini`: `%`, `@`, `:`, `/` в пароле
не требуют ручного URL-кодирования. Полная строка подключения не логируется.

Для новой миграции: `uv run --locked --env-file ../.env alembic revision --autogenerate -m '...'`.
Проверяйте результат вручную: Alembic не сравнивает содержимое CHECK-ограничений.
Статусы/роли/исходы хранятся как VARCHAR + CHECK, значения ORM берутся из API-схем.
Изменения enum/литералов требуют явной миграции CHECK и совместимого контракта.
`b03_initial` не импортирует живые ORM-модели и сохраняет историческое определение.

Обратные составные FK applications → decisions/offers добавляются после создания
всех таблиц и снимаются первыми при откате. FK не удаляют историю каскадно.
Сервисные инварианты следующих этапов описаны в `../docs/data-model.md`.

## Импорт аккаунтов и проверка входа

Импорт запускается явно и атомарно, только для аккаунтов из `seed/accounts.json`:
`clients` → client, `underwriters` → underwriter (9 + 1). Compose монтирует этот файл
в `/seed/accounts.json` read-only; build context остаётся `backend/`, Dockerfile не
копирует файлы из соседних каталогов. Файл должен быть читаем UID 10001 контейнера
(обычный Git checkout даёт 0644). В средах с исходным режимом 0600 используйте
временную читаемую копию и локальный Compose override mount, сохраняя оригинал.
Повторный импорт сохраняет UUID, имя, роль и
существующий хеш/пароль. Конфликт роли откатывает весь импорт с безопасной ошибкой.
Отчёт содержит только количества; пароли не печатаются. `company_inn` игнорируется:
это контекст демо, права определяются `applications.owner_id`, связи user → company нет.
Без дополнительных флагов компании, продукты и сценарии не загружаются.

```bash
# Из корня, после миграций:
docker compose run --rm backend python -m app.seed --accounts /seed/accounts.json
# Повтор: created=0, unchanged=10
# Локально из backend/:
uv run --locked --env-file ../.env python -m app.seed --accounts ../seed/accounts.json
```

Локальный curl-путь (Python нужен только для чтения JSON; JWT/CSRF не печатаются):

```bash
auth_origin=http://localhost:8000  # должен совпадать с AUTH_ALLOWED_ORIGIN
auth_cookies=$(mktemp)
chmod 600 "$auth_cookies"
csrf_token=$(curl --fail-with-body -sS -c "$auth_cookies" -b "$auth_cookies" \
  "$auth_origin/api/auth/csrf" | python -c 'import json,sys; print(json.load(sys.stdin)["csrf_token"])')
curl --fail-with-body -sS -c "$auth_cookies" -b "$auth_cookies" \
  -H "Origin: $auth_origin" -H "X-CSRF-Token: $csrf_token" -H 'Content-Type: application/json' \
  -d '{"login":"demo-approve","password":"demo-2026"}' "$auth_origin/api/auth/login"
# Старый CSRF после входа недействителен:
csrf_token=$(curl --fail-with-body -sS -c "$auth_cookies" -b "$auth_cookies" \
  "$auth_origin/api/auth/csrf" | python -c 'import json,sys; print(json.load(sys.stdin)["csrf_token"])')
curl --fail-with-body -sS -b "$auth_cookies" "$auth_origin/api/auth/me"
curl --fail-with-body -sS -c "$auth_cookies" -b "$auth_cookies" -X POST \
  -H "Origin: $auth_origin" -H "X-CSRF-Token: $csrf_token" "$auth_origin/api/auth/logout"
# Успех logout: 204, пустое тело. Следующий /me: 401.
rm "$auth_cookies"
unset csrf_token
```

Для банковской роли используйте `analyst`; локальные демонстрационные пароли —
в `docs/test-data.md`. Не публикуйте cookie jar, не используйте `curl -v` в общих логах.

Фронтенд: GET csrf → сохранить токен в памяти → POST login с X-CSRF-Token →
GET csrf заново → GET me → бизнес-запросы (все мутации с X-CSRF-Token) →
POST logout → очистить токен в памяти. Следующий GET csrf создаст анонимный контекст.
Повторный GET csrf действующего контекста возвращает тот же токен, сохраняя работу
других вкладок. При смене входа/выходе в другой вкладке перечитайте me и csrf.
Браузер сам отправляет Origin и cookies при same-origin запросах; EventSource для
GET не требует CSRF. JWT не читается JavaScript и не хранится в localStorage.

Переиспользуемые зависимости находятся в `app/dependencies.py`: текущие пользователь
и сессия, роль, CSRF/Origin, загрузка заявки с проверкой owner_id или доступа
underwriter, принадлежность документа заявке. Без сессии — 401, роль/CSRF/Origin —
403, чужая/отсутствующая заявка или документ — 404. Подмена document_id из другой
заявки не проходит даже при доступе к заявке из URL. Банковские операции доступны
только underwriter; клиентские команды изменения заявки ему запрещены.
Продукты, поиск компании и CRUD черновиков реализованы в B05, submit/cancel — в B06.
Остальные переходы, файлы и поток SSE идут далее; эти заглушки после доступа возвращают 501.

## B05: компании, продукты и черновики

Полный импорт выше загружает 10 аккаунтов, 9 компаний и 4 продукта одной транзакцией.
Можно выбрать отдельные источники; прежняя команда `--accounts` сохранена.
Повтор сохраняет UUID и хеши аккаунтов, UUID компаний/продуктов, не создаёт дубли и
не меняет версию неизменённого продукта. Конфликт/ошибка откатывает все выбранные
источники. Импорты сериализуются PostgreSQL advisory lock. Сценарии заявок и решения
из seed не импортируются.

Названия/описания берутся из `docs/product/ui-texts.ru.json`, цели и кредитные
условия — только из `seed/products.json` владельца рисков. `products.policy_snapshot`
сохраняет исходные строки условий и `policy_version`; изменение данных повышает
`products.version`. Миграция `b05_product_policy` добавляет только nullable JSONB,
старые миграции не изменены. Каталог отдаёт только публичные поля и активные продукты,
по code/id. Условия не проверяются при сохранении частичного черновика; полнота проверяется
в submit B06, кредитная допустимость — будущим подключением решения рисков.

Локальный импорт из `backend/`:

```bash
uv run --locked --env-file ../.env python -m app.seed \
  --accounts ../seed/accounts.json --companies ../seed/companies.json \
  --products ../seed/products.json --product-texts ../docs/product/ui-texts.ru.json
```

Compose монтирует все четыре источника read-only. Как и accounts, они должны
читаться UID 10001; для checkout с режимом 0600 используйте временные читаемые копии.
`MOCKS_BASE_URL` локально — `http://localhost:8001`, внутри Compose — `http://mocks:8000`.
`EGRUL_TIMEOUT=2` ограничивает весь запрос и HTTP I/O (0.1–10 секунд). Поиск всегда
обращается к `/egrul/{inn}` локального мока, исключает status/director из публичного
ответа. Только 404 источника превращается в `200 {"company": null}`. Таймаут, сбой,
невалидный ответ или несовпадение ИНН дают существующий контрактный `500 INTERNAL_ERROR`.
БД-транзакция авторизации завершается до HTTP; транзакционных блокировок в сети нет.

Контракт сохранён: lookup возвращает CompanyInput без id, черновик принимает company
объектом. Найденная компания сохраняется в БД; уникальный ИНН и UPSERT обеспечивают
одну identity при конкуренции. Создание/PATCH связывают company_id по ИНН и сохраняют
отдельный снимок введённой анкеты; пользовательский ввод не переписывает справочник.
Продукт обязан существовать и быть активным (иначе 422); product_snapshot включает
его версию и условия. Пропуск company/product сохраняет старый снимок, null очищает
ссылку и снимок. Изменение справочников не меняет ранее сохранённые анкеты.

После входа и обновления CSRF:

```bash
curl --fail-with-body -sS -b "$auth_cookies" "$auth_origin/api/products"
curl --fail-with-body -sS -b "$auth_cookies" \
  "$auth_origin/api/companies/lookup?inn=0086906651"
curl --fail-with-body -sS -b "$auth_cookies" -H "Origin: $auth_origin" \
  -H "X-CSRF-Token: $csrf_token" -H 'Content-Type: application/json' \
  -d '{}' "$auth_origin/api/applications"
# Используйте id ответа (Location: /api/applications/{id}):
application_id='UUID-из-ответа'
curl --fail-with-body -sS -b "$auth_cookies" -X PATCH \
  -H "Origin: $auth_origin" -H "X-CSRF-Token: $csrf_token" \
  -H 'Content-Type: application/json' \
  -d '{"expected_revision":1,"amount":"100000.01","purpose":"Закупка товара"}' \
  "$auth_origin/api/applications/$application_id"
curl --fail-with-body -sS -b "$auth_cookies" \
  "$auth_origin/api/applications?status=DRAFT&offset=0&limit=20"
curl --fail-with-body -sS -b "$auth_cookies" \
  "$auth_origin/api/applications/$application_id"
```

POST допускает `{}`, назначает owner_id из сессии, DRAFT/revision=1. PATCH меняет
только переданные поля, null очищает nullable-поле. Блокировка строки и повторное
чтение после неё атомарно проверяют DRAFT/expected_revision; конфликт — 409 без записи,
успех повышает revision и updated_at. Создание/редактирование и событие аудита
фиксируются одной транзакцией. Два запроса одной ревизии из разных сессий дают
один успех и один 409. Чужая заявка — 404; underwriter не получает клиентский CRUD.
Список фильтруется по владельцу/статусу, total и страница читаются одним SQL-снимком,
порядок updated_at DESC, id DESC. Карточка формирует только публичное решение и
allowed_actions по статусу; submit/cancel реализованы в B06.
Суммы проходят через Decimal/NUMERIC, время — UTC; все API-ответы имеют no-store.

## B06: отправка и отмена

Миграция `b06_submit_queue` добавляет снимки отправок, очередь, идемпотентность и
nullable `applications.input_revision`. Применённые миграции B03–B05 не меняются.
Команды миграций и seed выше сохраняются. Пример после login и обновления CSRF
(компанию и UUID активного продукта возьмите из lookup/catalog):

```bash
# Сначала создайте или заполните черновик: company, product_id,
# amount="100000.00", term_months=12, purpose="purchase_goods".
# expected_revision берите из последней карточки/PATCH.
submit_key=$(python -c 'import uuid; print(uuid.uuid4())')
curl --fail-with-body -sS -b "$auth_cookies" \
  -H "Origin: $auth_origin" -H "X-CSRF-Token: $csrf_token" \
  -H "Idempotency-Key: $submit_key" -H 'Content-Type: application/json' \
  -d '{"expected_revision":2,"consent_to_checks":true}' \
  "$auth_origin/api/applications/$application_id/submit"
# Успех: 202, status=SUBMITTED, revision=3, allowed_actions=["cancel"].
# После сетевой ошибки повторите ТОЧНО ту же команду с тем же ключом и телом.
curl --fail-with-body -sS -b "$auth_cookies" \
  -H "Origin: $auth_origin" -H "X-CSRF-Token: $csrf_token" \
  -H 'Content-Type: application/json' \
  -d '{"expected_revision":3,"reason":"Заявка больше не нужна"}' \
  "$auth_origin/api/applications/$application_id/cancel"
# 200, CANCELLED, revision=4, allowed_actions=[].
```

Владелец-client, обязательные Origin/CSRF, expected_revision; чужая заявка — 404,
неверная роль/Origin/CSRF — 403, статус/ревизия — 409. Submit только из DRAFT;
cancel из DRAFT, SUBMITTED, CHECKING, SCORING, NEEDS_INFO, MANUAL_REVIEW, APPROVED,
COUNTER_OFFER, OFFER_ACCEPTED. SIGNED, ISSUED, REJECTED, CANCELLED отменять нельзя.
Причина отмены остаётся во внутреннем audit_payload, публичный текст фиксирован.
История, решения и предложения не удаляются.

Полнота (рабочее предложение бэкенда, не утверждение кредитной политики рисков):
компания с ИНН по действующему формату, существующий активный продукт, непустая
после strip цель, положительные сумма/срок, consent_to_checks=true. Пробелы цели
не изменяются при сохранении. Нет/неверно согласие и неполная анкета — 422.
Выручка может отсутствовать: decide() обрабатывает это через NEEDS_INFO. Контрольные
цифры ИНН, лимиты продукта и подбор контрпредложения не становятся запретами submit.

Цель остаётся свободным текстом API. Словарь рисков использует buy_equipment,
cash_gap, purchase_goods, expand_business, urgent_expenses, other; для интеграции
предпочтительны эти значения. B06 не превращает поле в enum и не заменяет текст
на other. В B08 неизвестная строка даёт технический failed/unsupported_purpose,
без кредитного решения, с сохранением исходного текста. Фронтенду до согласования
отображения свободного ввода следует отправлять эти шесть кодов без изменения.
Расширение API до enum или согласованное отображение текста требует отдельного
контрактного PR с продуктом/рисками; в B08 контракт не меняется. Рекомендации seed/risk-review не
меняют рабочие seed, действующую политику и контракт в этом PR.

Idempotency-Key — UUID одной попытки, область `(пользователь, заявка, submit, UUID)`.
Успешный результат хранится минимум 24 часа; в B06 автоматической очистки нет.
Сравнивается валидированное тело как JSONB: пробелы и порядок ключей не важны.
Повтор после авторизации, владения и CSRF, но до статуса/ревизии возвращает исходный
202 и сохранённую карточку, в том числе после отмены/рестарта. Затем GET карточки
даёт актуальный статус. То же значение с другим телом — 409. Для новой попытки
после изменения черновика нужен новый ключ; новый ключ не обходит статус/ревизию.
Не заменяйте ключ после неизвестного результата сети. JWT/cookies/CSRF не сохраняются
в записи результата. Ошибка транзакции не резервирует ключ.

Снимок отправки содержит анкету, выбранный в B05 снимок продукта с версией/политикой,
согласие и серверное время UTC. Активность продукта повторно проверяется под
shared lock; ранее выбранные условия не обновляются молча из каталога.
Worker должен читать этот снимок. submitted_on для интерфейса рисков выводится
из submitted_at в UTC; document_types и ответы источников B06 не добавляет.
Версия политики в снимке не переключает константы decide(): B08 обязан проверить
совместимость с версией модуля рисков, не применять новую политику незаметно.

Очередь и контракт B07, включая lease, ретраи, порядок блокировок и проверку
актуальности: [docs/data-model.md](../docs/data-model.md#технические-сущности-b06).
Submit, переход, аудит, снимок входов, задание и исходный ответ фиксируются одной
транзакцией; вспомогательные сервисы не вызывают commit. После B06 автоматического
кредитного решения ещё нет. Универсального публичного endpoint переходов нет.

## Проверки

Из `backend/`:

```bash
uv run --locked pytest -q
uv run --locked ruff check . ../scripts
uv run --locked ruff format --check . ../scripts
uv run --locked python -m app.export_openapi --check
```

Обычный pytest пропускает PostgreSQL-проверки с явной причиной. Для них нужна
настоящая PostgreSQL **16** и пользователь с `CREATE DATABASE`; SQLite не используется.
Запустите выделенный Compose project из корня (свободный порт можно заменить):

```bash
docker compose --env-file .env -p b03-tests up -d --wait db
cd backend
uv run --locked --env-file ../.env pytest -q --run-postgres
```

Если db этого проекта конфликтует по порту с уже работающей БД, задайте свободный
`POSTGRES_PORT` в отдельном env-файле и передайте тот же файл обеим командам.
Флаг `--run-postgres` включает модели, авторизацию, импорт, черновики, B06–B08:
submit/cancel, повтор после рестарта, гонки с разными сессиями авторизации,
ошибки PostgreSQL при записи аудита/снимка/задания/ответа и атомарные откаты.
Фикстура подключается к указанной административной БД, проверяет версию, создаёт
случайную `b03_test_<uuid>` через `TEMPLATE template0`, выполняет
`upgrade b03_initial → запись тестовой строки → upgrade head → проверка сохранности →
downgrade b05_product_policy → проверка удаления таблиц B06 и сохранности данных →
upgrade head/check → downgrade b03_initial → проверка сохранности →
upgrade head → upgrade head → check →
downgrade base → upgrade head → check`.
После downgrade проверяется отсутствие бизнес-таблиц. После тестов удаляется только
созданная фикстурой БД. Проверки охватывают частичный черновик, commit/read, Decimal,
UTC, DATE/JSONB, UNIQUE/CHECK/NOT NULL/FK, чужие решения/предложения и защиту истории.

Ручной downgrade допустим только на отдельной одноразовой БД, которую вы создали
для проверки; он удаляет таблицы и данные:

```bash
uv run --locked --env-file ../.env.test alembic downgrade base
uv run --locked --env-file ../.env.test alembic upgrade head
```

`.env.test` должен указывать на эту одноразовую БД. Не запускайте downgrade на БД
разработчиков. Удаление существующих volumes для проверки не требуется.

Авторизация проверяется настоящими HTTP-запросами TestClient к приложению с реальной
PostgreSQL: обе роли, полный вход/выход, рестарт приложения, истечение/подделка JWT,
issuer/audience/algorithm/claims, отзыв и подмена сессии, CSRF и точный Origin,
ротация, cookies, владелец/другой клиент/банк на всех бизнес-маршрутах, подмена документа.

Docker-проверка создаёт случайный отдельный Compose project, порты, пароль и секрет;
удаляет только собственные ресурсы. Запускает миграции и двойной импорт до API,
проверяет login → продукты → компанию → создание/PATCH черновика → submit → повтор →
одно задание и аудит в БД → рестарт → повтор → cancel → аудит и отменённое задание.
Также сохраняются проверки logout/JWT replay, отказа мока, UID и health при сбое БД.
Проверки worker B07 и ограничения кредитного сценария описаны ниже:

```bash
uv run --locked python ../scripts/check_docker.py
# При HTTPS inspection proxy передайте CA только BuildKit secret:
DOCKER_BUILD_CA=/path/to/combined-ca.pem uv run --locked python ../scripts/check_docker.py
```

Полный Ruff check применяется ко всему backend. Форматирование модуля рисков
принадлежит его владельцу; проверка принадлежащих бэкенду файлов исключает app/decision:

```bash
uv run --locked ruff format --check . ../scripts --extend-exclude app/decision
```

## B07: отдельный worker проверок

После явных миграций и seed (команды выше):

```bash
docker compose build backend mocks
docker compose up -d --wait backend mocks
docker compose up -d --scale worker=2 worker
docker compose logs -f worker
# Вне Docker из backend/:
uv run --locked --env-file ../.env python -m app.worker
```

Worker и API используют один образ backend, Settings и async session factory.
FastAPI не запускает worker; миграций/seed внутри worker нет. Нужен доступ только
к PostgreSQL и локальному `MOCKS_BASE_URL`. Один процесс последовательно исполняет
задания; несколько процессов безопасно разделяют очередь через SKIP LOCKED.
После submit: SUBMITTED → CHECKING → SCORING → один из пяти исходов B08.
Фаза checks сохраняет ответы; фаза decision того же задания вызывает decide() без HTTP.

| Переменная (секунды, кроме attempts) | Default | Допустимый диапазон |
| --- | --- | --- |
| `WORKER_POLL_INTERVAL` | 1 | 0.05–60 |
| `WORKER_HTTP_TIMEOUT` | 3 | 0.1–30 на сетевую операцию |
| `WORKER_ATTEMPT_TIMEOUT` | 30 | 1–300 на всю попытку |
| `WORKER_DB_TIMEOUT` | 5 | 0.1–30 на SQL/ожидание блокировки |
| `WORKER_LEASE_SECONDS` | 45 | 2–600; минимум attempt + db + 5 |
| `WORKER_MAX_ATTEMPTS` | 5 | 1–20 |
| `WORKER_RETRY_BASE` | 2 | 0.1–300, не больше retry_max |
| `WORKER_RETRY_MAX` | 30 | 0.1–3600 |

Все числа конечны и валидируются при старте, включая соотношения lease/backoff.
HTTP выполняется последовательно одним переиспользуемым AsyncClient; общий deadline
ограничивает и медленную потоковую передачу. Lease не продлевается: обычная попытка
ограничена 30 секундами при lease 45. DB-транзакции дополнительно ограничены общим
client-side timeout `WORKER_DB_TIMEOUT + DB_READINESS_TIMEOUT + 2`, включая commit;
statement_timeout/lock_timeout действуют только в worker-транзакциях.
SIGTERM/SIGINT прекращает polling после текущей ограниченной попытки; клиент и engine
закрываются. Compose даёт 45 секунд на остановку при defaults. Если увеличиваете
таймауты, увеличьте stop_grace_period локальным override. SIGKILL/авария оставляют
running для восстановления по lease. Ошибка БД не считается успехом: supervisor
логирует категорию и продолжает polling; задание восстанавливается по сроку lease.

Временные network/timeout/429/5xx → pending с задержкой
`min(retry_max, retry_base * 2^(attempts-1))`. Defaults: 2, 4, 8, 16 секунд между
пятью попытками. Каждый захват, включая восстановление после падения, расходует
попытку; если последний исполнитель погиб, следующий захват только фиксирует failed,
HTTP сверх бюджета не выполняется. Каждая новая попытка заново запрашивает **все три
источника**; частичные ответы в БД не сохраняются. Это простой согласованный набор
одной попытки, без смешивания ответов разного возраста. Во время HTTP, backoff и
polling нет открытой транзакции. Пустой polling не пишет аудит.

Все поля риска обязательны, bool/int не преобразуются из строк, деньги — конечные
неотрицательные строки с двумя знаками; ИНН обязан совпадать. Для ЕГРЮЛ проверяются
известные статусы ACTIVE/LIQUIDATED/LIQUIDATING/BANKRUPT/INACTIVE, реальная дата и ОКВЭД.
Валидный `pb.available=false` с nullable финансовыми данными передаётся дальше без
подмены значений. Отсутствующие поля риска не получают благоприятных defaults.
Автозаполнение B05 использует прежний отдельный адаптер и контракт.

**Рабочее решение до согласования команды:** 404, несовпадение ИНН, повреждённый JSON,
отсутствующие/некорректные поля и прочие постоянные HTTP-ошибки сразу дают техническое
failed; заявка остаётся CHECKING. Исчерпание временных повторов тоже даёт failed.
REJECTED не используется, кредитное решение не создаётся. В `pipeline_jobs` сохраняются
только `last_error_stage/category`, без ответов/исключений/секретов. Логи содержат
job/application/submission ID, попытку, фазу, этап и категорию. Не выводите ответы источников
в публичные события, карточку, диагностические сообщения или логи.

### Диагностика и повтор технически неуспешной проверки

```bash
docker compose logs --tail=100 worker
docker compose exec db psql -U alfa_hack -d alfa_hack -c \
  'SELECT id,application_id,submission_id,phase,state,attempts,available_at,lease_expires_at,last_error_stage,last_error_category FROM pipeline_jobs ORDER BY created_at DESC;'
```

Для своих значений POSTGRES_USER/DB замените аргументы psql. Сначала устраните причину:
состояние mocks/db, доступность endpoints, формат данных, настройки timeout. Просмотрите
аудит по application_id; переходы и запланированные повторы содержат submission/job,
номер попытки и категорию. Не меняйте анкету/снимок задним числом, не делайте повторный
submit и не переводите заявку в REJECTED.

После устранения причины оператор может явно выдать новый ограниченный бюджет:

```bash
docker compose run --rm --no-deps backend python -m app.requeue UUID-ЗАДАНИЯ
```

Команда блокирует сначала заявку, затем задание и перечитывает их. Разрешён только
failed/checks для актуальной отправки в CHECKING без сохранённых проверок либо
failed/decision в SCORING с сохранёнными проверками и без решения этой отправки.
Одна транзакция пишет аудит operator_requeue с предыдущей ошибкой/attempts:
checks — CHECKING → CHECKING; decision — application_updated в SCORING без нового
перехода статуса. Команда повышает revision, сохраняет input_revision, сбрасывает attempts,
lease/ошибку и делает тот же job pending сейчас. Она не создаёт второй job, не
оживляет отменённую/устаревшую отправку и не повторяет завершённое решение. Гонки с cancel
сериализуются теми же блокировками. Публичного API повторного запуска нет.

### Хранение проверок

`b07_worker_checks` добавляет `submission_checks` и phase/errors в pipeline_jobs.
Один результат (PK submission_id, FK без CASCADE) содержит оригинальные проверенные
JSONB egrul/pb/bki, schema_version=1 и created_at. Успех одной транзакцией сохраняет
весь набор, SCORING/аудит и **тот же job**: phase=decision, state=pending, attempts=0,
available_at=DB-now, обе lease=NULL. Обе фазы выбираются по одной записи через
LIMIT 1 + SKIP LOCKED (исправление PR #58 сохранено).

## B08: интеграция решения

**Требования документов:** чистая функция рисков, пять исходов, неизменяемая история,
аудит переходов, версия входов, защита отмены/lease и отдельная публичная проекция.
**Технические решения B08:** точное совпадение версии политики с загруженным модулем,
проверка параметров продукта под тем же version label, JSONB полного результата,
диагностируемые несовместимости и операторский requeue. Это не новые кредитные правила.
Модуль `app/decision/`, рабочие seed и материалы `seed/risk-review` не изменяются;
последние не загружаются как политика или новый набор фикстур.

`decision_adapter.build_inputs()` читает только application_submissions:
code из product_snapshot, сумму/срок/цель/выручку/legal_form из form_snapshot,
submitted_on из submitted_at.astimezone(UTC).date(). Деньги преобразуются в Decimal.
Текущие поля заявки и исходного продукта не заменяют снимки. annual_revenue=null
передаётся модулю и может дать NEEDS_INFO. document_types явно пустой: поддерживается
первичная отправка; файлы и повторная отправка после дополнения относятся к B11.
Три отчёта читаются только из submission_checks версии 1, повторно проверяются на
формат/ИНН и преобразуются в типы рисков. Фаза decision не вызывает mocks.

Настоящий `decide(application, bki, egrul, pb)` выполняется вне транзакции, в потоке.
При превышении timeout поток может закончить чистый расчёт позднее, но его результат
не сохраняется. Захват истёкшей lease создаёт новый токен и расходует бюджет текущей
фазы. Перед сохранением блокируются заявка → job, перечитываются отправка, версия
входов, SCORING, phase=decision, running, токен и срок lease. revision состояния
не сравнивается с input_revision. После flush результата/перехода/аудита условный
UPDATE на DB-clock повторно проверяет lease, фазу, ссылки и версию входов. Неудача
откатывает всю транзакцию. Повтор после commit и запоздалый исполнитель не пишут дублей.

Одной транзакцией создаются Decision, Offer при APPROVED/COUNTER_OFFER,
current_decision_id, переход через существующий transition, аудит и completed с
очисткой lease/ошибки. Остальные исходы — NEEDS_INFO, MANUAL_REVIEW, REJECTED — без
предложения. Результат проверяется существующей публичной схемой до commit.
Если предложен исходный продукт, сохраняется снимок отправки; при другом продукте
code разрешается в UUID активного каталожного продукта под shared lock, проверяются
версия и параметры его политики и сохраняется **его** снимок. expires_at=null:
согласованного срока действия нет. Принятие предложения/оформление не реализуются.

Новая миграция `b08_decision_result` поверх B07, прежние миграции сохранены:

| Поле decisions | Назначение |
| --- | --- |
| submission_id | Nullable UUID, UNIQUE и составной FK (application_id, submission_id) к отправке. Не более одного автоматического решения на отправку; старая история и будущие ручные решения допускают null. |
| risk_result | Nullable JSONB полного dataclass Decision: публичные поля и score, risk_class, requested_dscr, offer_dscr, data_gaps, scoring_factors, stop_factors, review_reason, analytical_explanation, offer.monthly_payment и остальные поля. Decimal — точные строки, tuple — JSON-массивы. Null только для прежней истории. |

Публичные поля, scoring_factors и analytical_explanation также заполняют существующие
колонки ORM. GET карточки читает явный список публичных полей и условий: внутренний
risk_result, баллы/класс/DSCR, факторы, политика и сырые отчёты клиенту не возвращаются.
Monthly payment сохраняется в risk_result, но публичный OfferTerms этого поля не имеет.
Банковский API, SSE, документы, действия андеррайтера, принятие, подписание и выдача
остаются следующими этапами. Изменений публичного OpenAPI нет.

### Ошибки и повтор decision

Технические ошибки оставляют заявку в SCORING, без нового решения/предложения.
В pipeline_jobs и логах только ограниченные коды; исходная цель остаётся в снимке,
тексты исключений и банковские данные не логируются.

| last_error_category | Действие оператора / зависимость |
| --- | --- |
| unsupported_purpose | Фронтенду отправлять известный код; продукту/рискам согласовать отображение свободного текста. Не изменять сохранённую отправку и не подставлять other. Для старой строки нужен совместимый адаптер после согласования. |
| incompatible_policy / incompatible_result_policy | Нужен модуль соответствующей политики или согласованная поддержка версий; не применять текущую политику молча. |
| unsupported_product / invalid_product_snapshot | Проверить источник и формат снимка; не подменять текущим каталогом. |
| missing_checks / unsupported_checks_schema / invalid_inputs / invalid_data | Проверить целостность сохранённых данных, schema_version, формат и ИНН. Не переписывать снимки задним числом; восстановление повреждённого хранения требует отдельной диагностики. |
| offered_product_unavailable | Проверить наличие/активность предложенного продукта и его политику в каталоге. |
| calculation_error / invalid_result | Исправить несовместимость модуля/адаптера или результата с публичной схемой вместе с владельцем рисков. |
| attempt_timeout / attempts_exhausted | Timeout повторяется в пределах WORKER_MAX_ATTEMPTS с прежним backoff; затем failed. Падения/ошибки БД тоже расходуют бюджет через повторный захват lease. |

Постоянные несовместимости сразу дают failed; REJECTED/MANUAL_REVIEW для них не
используются. После устранения причины `python -m app.requeue UUID` выдаёт новый
ограниченный бюджет **той же фазы**, не запрашивая источники для decision.
Временный retry decision меняет только техническое задание, без выдуманного
SCORING → SCORING; операторский requeue дополнительно пишет application_updated.
При ошибке БД транзакция откатывается, supervisor пишет безопасную категорию,
running восстанавливается по lease. Если запись failed тоже не удалась, она будет
повторена после восстановления БД и lease, без фиксации кредитного результата.

### Проверки B08

```bash
cd backend
uv run --locked --env-file /path/to/isolated-test.env pytest -q --run-postgres
uv run --locked ruff check . ../scripts
uv run --locked ruff format --check . ../scripts --extend-exclude app/decision
uv run --locked python -m app.export_openapi --check
git diff --check
```

PostgreSQL-фикстуры создают случайные БД и удаляют только их. Тесты B08 используют
настоящий decide() для всех девяти рабочих компаний (пять исходов и три вида
контрпредложения), проверяют полную запись/публичную проекцию, двух worker,
отмену/смену входов/lease, падения/повторы, откаты и диагностируемые несовместимости.
Отдельный test_decision_migration_postgres создаёт ещё одну одноразовую БД:
B07 с решением/предложением → upgrade B08 → repeat/check → запись новых полей →
downgrade B07 → проверка сохранности публичного решения/предложения/ссылки →
upgrade/repeat/check. Downgrade удаляет только новые поля и их ограничения;
внутренний risk_result и связь submission_id при откате теряются. На рабочей БД
такой откат без отдельного плана сохранения этих данных не выполняйте.

### Проверка Docker B08

`python ../scripts/check_docker.py` теперь сохраняет прежний B04–B06 smoke и добавляет
два реальных worker, CHECKING → SCORING → настоящее решение и клиентскую карточку,
все девять рабочих сценариев, включая три контрпредложения и demo-bki с двумя
503/аудированными повторами и последующим APPROVED, SIGTERM/restart после commit,
SIGKILL во время HTTP/восстановление lease, отмену с поздним ответом, остановку БД и
возобновление обработки. Ожидания ограничены deadline и проверяют условие. Используются
только собственные случайные Compose project/порты/volume; удаляются только они.
В smoke миграции и seed явно проверяются до worker; сервис migrate также использует
временные читаемые копии seed для UID 10001, как backend. Общий docker compose up
автоматически выполняет migrate по правилам PR #61.

### Политика draft2 и транспорт проверок

PR #59 обновил политику до 2026-10-22.draft2; интеграция B08 находится в main через #64.
result_snapshot автоматически сохраняет requested_dscr, offer_dscr и data_gaps;
отдельная миграция для новых полей dataclass не требуется.
Старые отправки draft1 на процессе draft2 безопасно получают incompatible_policy.

В draft2 чистый модуль допускает неизвестные налоговые/регистрационные флаги ФНС
и умеет возвращать incomplete_checks. Сохранённый транспорт schema_version=1 из B07
требует non-null tax_arrears/mass_address/director_disqualified; рабочие mocks им
соответствуют. Пропуск/null такого поля остаётся техническим invalid_data. Для
передачи подобных неполных отчётов требуется согласовать с рисками транспортную
схему/версию; расширение не включается молча в существующий формат проверок.

Диагностика конкурентного lookup B05 наблюдает PID именно двух участвующих API-пулов.
Она не считает транзакции других TestClient в общей тестовой БД; оба lookup остаются
за барьером до завершения наблюдений. Проверки отсутствия транзакций во время HTTP
и единственной company identity сохранены.
