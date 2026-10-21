# Умный кредитный кабинет предпринимателя

Прототип цифрового кредитного конвейера для малого и микробизнеса. Кейс Alfa Hack 2026 (Changellenge × Альфа-Банк).

## Что делаем

Единый рабочий процесс из двух частей:

- **Веб-кабинет клиента**: выбор кредитного продукта, подача заявки, отслеживание статуса и получение решения.
- **Банковская обработка**: этапы конвейера, проверки, скоринг и принятие решения. Статус заявки меняется в реальном времени, данные сохраняются.

Решение не бинарное: **одобрение**, **контрпредложение** (сумма, срок или другой продукт), **запрос дополнительных данных**, **отказ**.

Внешние проверки (БКИ, ЕГРЮЛ/ЕГРИП, ФНС «Прозрачный бизнес») работают через локальные моки. Подписание и выдача могут быть симулированы.

## Ограничения кейса

- Только веб-интерфейс, без мобильного приложения.
- Никаких платных API, подписок и лицензий.
- LLM, если используется, только open-weights.
- Весь сценарий внутри продукта, без email, SMS и мессенджеров.
- Запуск в Docker одной воспроизводимой командой на обычном ПК.

## Обязательные артефакты к сдаче

- [ ] Docker / docker-compose, lock-файлы зависимостей
- [ ] README: назначение, архитектура, окружение, запуск, переменные окружения, компоненты, пошаговый сценарий проверки
- [ ] Синтетические данные и тестовые аккаунты для сквозного сценария
- [ ] `THIRD_PARTY_NOTICES` (включая замоканные интеграции)
- [ ] Схема архитектуры, описание API, OpenAPI/Swagger, коллекция запросов
- [ ] Диаграмма состояний заявки, модель данных, тестовые сценарии

Документация собирается в [`docs/`](docs/), моки внешних сервисов в [`mocks/`](mocks/).

## Сроки

Дедлайн: **26 октября 2026**. Оценивается состояние кода на момент дедлайна (коммит/тег). Если репозиторий приватный, нужно выдать доступ аккаунту жюри `alfa-hack-2026`.

## Запуск

Реализованы B01–B05: инфраструктура, API-контракт, ORM/миграции, авторизация,
импорт аккаунтов/компаний/продуктов, поиск компании через локальный мок ЕГРЮЛ и CRUD
черновиков в PostgreSQL. Моки и модуль решений добавлены участником по рискам;
submit/cancel, подключение конвейера, worker и SSE ещё впереди.
Текущий статус: [план бэкенда](backend/PLAN.md).

### Стек и компоненты

- `backend`: Python 3.12, FastAPI, Uvicorn, async SQLAlchemy 2 и asyncpg.
- `db`: PostgreSQL 16, данные в именованном Docker volume `postgres_data`.
- Зависимости backend управляются через uv, точные версии зафиксированы в
  [`backend/uv.lock`](backend/uv.lock). Docker устанавливает их с `--locked`.

Backend создаёт async engine и фабрику сессий на время жизни приложения и
освобождает пул при завершении. Таблицы создаются явным запуском Alembic. При обычном запуске
Compose ждёт healthcheck PostgreSQL перед запуском backend; уже работающий
backend продолжает отвечать на liveness при отказе БД.

### Docker

Нужны Docker Engine с BuildKit и Docker Compose v2.
Из корня репозитория:

```bash
cp .env.example .env
# Сгенерируйте секрет и внесите его в AUTH_JWT_SECRET в .env:
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

В `.env` задайте собственный `POSTGRES_PASSWORD` и сгенерированный
`AUTH_JWT_SECRET` (минимум 32 символа). Для локального HTTP оставьте
`AUTH_COOKIE_SECURE=false`, `AUTH_ALLOWED_ORIGIN=http://localhost:8000`.
При смене порта измените также origin. Секреты не коммитить.
Затем из корня:

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

Миграции и seed пока выполняются явно; автоматизация первого запуска — B10.
Импорт атомарный и повторяемый: 10 аккаунтов, 9 компаний, 4 продукта; повтор без дублей.
UUID/хеши аккаунтов сохраняются. Четыре исходных файла должны читаться UID 10001;
для нестандартных прав файла см. [инструкцию бэкенда](backend/README.md).
Для запуска также моков:

```bash
docker compose up --build --wait
```

Повторный запуск после настройки `.env` — одной командой `docker compose up --build --wait`.
Swagger UI: <http://localhost:8000/docs>, схема OpenAPI: <http://localhost:8000/openapi.json>.
Сервисы доступны только на loopback; для публичного развёртывания нужна отдельная настройка.

```bash
docker compose down     # остановить, сохранить данные
```

`docker compose down --volumes` также удаляет данные PostgreSQL.

### Окружение

Скопируйте [`.env.example`](.env.example), задайте пароль БД и `AUTH_JWT_SECRET`. Пароль в примере
предназначен только для локальной разработки. `.env` не коммитится и не попадает в образ.
Настройки проверяются при старте backend; отсутствие пароля или неверные значения
приводят к ошибке конфигурации.

| Переменная | Назначение | Значение в примере |
| --- | --- | --- |
| `POSTGRES_DB` | Имя БД | `alfa_hack` |
| `POSTGRES_USER` | Пользователь БД | `alfa_hack` |
| `POSTGRES_PASSWORD` | Обязательный пароль БД | Локальный пример в `.env.example` |
| `POSTGRES_HOST` | Адрес БД при запуске вне Docker | `localhost` |
| `POSTGRES_PORT` | Порт БД на хосте | `5432` |
| `DB_READINESS_TIMEOUT` | Таймаут операции проверки БД, секунды (0.1–10) | `2` |
| `BACKEND_PORT` | HTTP-порт backend на хосте | `8000` |
| `AUTH_JWT_SECRET` | Обязательный случайный секрет JWT/CSRF, минимум 32 символа | Пусто, нужно сгенерировать |
| `AUTH_ALLOWED_ORIGIN` | Точный origin для мутаций, без завершающего `/` | `http://localhost:8000` |
| `AUTH_COOKIE_SECURE` | Только HTTPS cookies; `false` для локального HTTP | `false` |

Все настройки авторизации и проверка входа: [backend/README.md](backend/README.md).

Внутри Compose backend всегда подключается к `db:5432`, независимо от порта на хосте.
Пароль передаётся в SQLAlchemy URL отдельным полем, поэтому спецсимволы не требуют
ручного URL-кодирования. Изменение реквизитов в `.env` не меняет пользователя и пароль
в уже созданном volume PostgreSQL.

### Локальная разработка

Нужны Python 3.12 и uv (Dockerfile использует uv 0.12.23).
После создания `.env`:

```bash
docker compose up -d --wait db mocks
cd backend
uv sync --locked
uv run --locked --env-file ../.env alembic upgrade head
uv run --locked --env-file ../.env python -m app.seed \
  --accounts ../seed/accounts.json --companies ../seed/companies.json \
  --products ../seed/products.json --product-texts ../docs/product/ui-texts.ru.json
uv run --locked uvicorn app.main:app --env-file ../.env --reload
```

### Проверки

```bash
cd backend
uv run --locked ruff check . ../scripts
uv run --locked ruff format --check . ../scripts
uv run --locked pytest -q
```

Pytest проверяет liveness, readiness при реальном отказе подключения asyncpg,
Swagger/OpenAPI и настройки окружения. Проверки миграций и авторизации с реальной
PostgreSQL 16 включаются отдельно через `--run-postgres`; без флага они пропускаются.
Настройка изолированной БД описана в [backend/README.md](backend/README.md).
Из корня репозитория полный Docker smoke-тест:

```bash
python3 scripts/check_docker.py
```

Скрипт собирает образ, поднимает отдельный Compose project на свободных портах,
проверяет Python 3.12, PostgreSQL 16, непривилегированного пользователя backend,
эндпоинты, отказ/таймаут БД, запуск backend без БД и восстановление readiness.
В конце удаляет только контейнеры, сеть и volume своего временного проекта.
При необходимости доверенного CA для HTTPS-прокси сборки:
`DOCKER_BUILD_CA=/path/to/ca-bundle.pem python3 scripts/check_docker.py`.

Ручной сценарий для уже запущенного Compose:

```bash
curl -i http://localhost:8000/health/live   # 200 {"status":"ok"}
curl -i http://localhost:8000/health/ready  # 200 {"status":"ok"}, реальный SELECT 1
docker compose stop db
curl -i http://localhost:8000/health/live   # 200 {"status":"ok"}
curl -i http://localhost:8000/health/ready  # 503 {"status":"unavailable"}
docker compose start db
# После готовности PostgreSQL следующий запрос снова вернёт 200:
curl -i http://localhost:8000/health/ready
```

Операция readiness ограничена `DB_READINESS_TIMEOUT`; при отмене зависшего запроса
SQLAlchemy/asyncpg может дополнительно потратить до 2 секунд на закрытие соединения
(при значении по умолчанию ответ приходит примерно за 4 секунды).
Ответ не раскрывает параметры подключения или текст ошибки драйвера.
Docker healthcheck backend использует `/health/ready`;
статус `unhealthy` при отказе БД не останавливает процесс.
