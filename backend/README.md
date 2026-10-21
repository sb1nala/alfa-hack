# Backend

Python 3.12, FastAPI, async SQLAlchemy 2/asyncpg, PostgreSQL 16, Alembic, uv.
B03 добавляет только схему хранения; бизнес-маршруты продолжают возвращать `501`.
Следующий этап — B04 (авторизация). Контракт: `../docs/openapi.yaml`.

## Миграции

Из корня репозитория, с настроенным `.env` по `.env.example`:

```bash
docker compose up -d --wait db
docker compose build backend
docker compose run --rm backend alembic upgrade head
docker compose run --rm backend alembic check
docker compose up -d backend
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
Флаг `--run-postgres` включает 59 интеграционных проверок вместе с обычными тестами.
Фикстура подключается к указанной административной БД, проверяет версию, создаёт
случайную `b03_test_<uuid>` через `TEMPLATE template0`, выполняет
`upgrade head → upgrade head → check → downgrade base → upgrade head → check`.
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
