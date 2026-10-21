# Backend

Python 3.12, FastAPI, async SQLAlchemy 2/asyncpg, PostgreSQL 16, Alembic, uv.
B01–B05: авторизация, JWT-сессии, CSRF, роли, каталог продуктов, поиск компании через
локальный мок ЕГРЮЛ и черновики в PostgreSQL (создать, список, карточка, PATCH).
Переходы submit/cancel, банковская очередь, worker, файлы и SSE пока возвращают `501`
после проверок доступа. Следующий этап — B06.
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

Из корня репозитория, с настроенным `.env` по `.env.example`:

```bash
docker compose up -d --wait db
docker compose build backend mocks
docker compose run --rm backend alembic upgrade head
docker compose run --rm backend alembic check
docker compose run --rm backend python -m app.seed \
  --accounts /seed/accounts.json --companies /seed/companies.json \
  --products /seed/products.json --product-texts /seed/product-texts.json
docker compose up -d --wait backend mocks
```

Образ содержит Alembic и миграции, работает от пользователя `app` (UID 10001).
Старт API не запускает миграции и не вызывает `create_all`. Healthchecks и Swagger
`http://localhost:8000/docs` сохранены. Автоматизация миграций стенда — этап B10.

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
Продукты, поиск компании и CRUD черновиков реализованы в B05. Бизнес-переходы,
файлы и поток SSE идут далее; эти заглушки после проверок доступа возвращают 501.

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
по code/id. Условия не проверяются при сохранении частичного черновика; полнота и
кредитная допустимость относятся к submit/решению следующих этапов.

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
allowed_actions по статусу; подсказки submit/cancel пока ведут к заглушкам B06.
Суммы проходят через Decimal/NUMERIC, время — UTC; все API-ответы имеют no-store.

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
Флаг `--run-postgres` включает интеграционные проверки моделей, авторизации и импорта.
Фикстура подключается к указанной административной БД, проверяет версию, создаёт
случайную `b03_test_<uuid>` через `TEMPLATE template0`, выполняет
`upgrade b03_initial → запись тестовой строки → upgrade head → проверка сохранности →
downgrade b03_initial → проверка сохранности → upgrade head → upgrade head → check →
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
проверяет login → продукты → компанию → создание/PATCH/чтение черновика → рестарт,
сохранность сессии и данных, logout/JWT replay, отказ мока, UID и health при сбое БД:

```bash
uv run --locked python ../scripts/check_docker.py
# При HTTPS inspection proxy передайте CA только BuildKit secret:
DOCKER_BUILD_CA=/path/to/combined-ca.pem uv run --locked python ../scripts/check_docker.py
```

Полный Ruff check применяется ко всему backend. В актуальном main есть существующее
отклонение форматирования `app/decision/texts.py`, принадлежащего рискам; B05 его не
меняет. Проверка всех файлов вне этой папки:

```bash
uv run --locked ruff format --check . ../scripts --extend-exclude app/decision
```
