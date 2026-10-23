# Инструкции для ИИ-агентов (Codex, Claude Code)

Этот файл читает Codex; `CLAUDE.md` подключает его для Claude Code через `@AGENTS.md`. Правила пишем только здесь.

## Проект

Прототип «Умный кредитный кабинет предпринимателя» для Alfa Hack 2026: веб-кабинет клиента (МСП) + банковская обработка заявки (проверки, скоринг, решение). Дедлайн — 26.10.2026, оценивается состояние `main` на момент дедлайна.

Подробный план команды: [`docs/team-plan.md`](docs/team-plan.md). Статусы заявки: [`docs/application-states.md`](docs/application-states.md). Архитектура: [`docs/architecture.md`](docs/architecture.md).

## Жёсткие ограничения кейса

- Только веб, без мобильного приложения.
- Никаких платных API, подписок и лицензий. Модели (LLM, OCR и др.) — только с открытыми весами; LLM подключается через OpenAI-совместимый API с настраиваемыми адресом, токеном и названием модели.
- Весь сценарий внутри продукта: без email, SMS, мессенджеров.
- Внешние сервисы (БКИ, ЕГРЮЛ/ЕГРИП, ФНС «Прозрачный бизнес») — только локальные моки из `mocks/`.
- Данные только синтетические.
- Всё запускается одной командой `docker compose up` на обычном ПК; версии зафиксированы lock-файлами.

## Стек

| Часть | Технологии |
| --- | --- |
| Бэкенд | Python 3.12, FastAPI, SQLAlchemy 2 (async), Alembic, PostgreSQL 16, uv (`uv.lock`), pytest |
| Фронтенд | Vite, React 18, TypeScript, React Router, TanStack Query, MUI, pnpm (`pnpm-lock.yaml`) |
| Моки | FastAPI, данные из JSON-фикстур |
| Инфраструктура | Docker, docker compose: db, migrate, backend, worker, mocks, frontend |

## Кто за какие папки отвечает

| Папка | Роль |
| --- | --- |
| `backend/` (кроме `backend/app/decision/`), `docker-compose.yml`, `.github/` | Бэкенд |
| `frontend/` | Фронтенд |
| `mocks/`, `backend/app/decision/`, `seed/`, `ml/` | Риски и данные |
| `docs/`, `README.md`, `THIRD_PARTY_NOTICES.md`, `api-collection/` | Продукт |

Не правьте чужие папки без согласования с владельцем.

## Правила

- В `main` — только через PR, ветки `feat/<область>-<задача>` или `docs/<тема>`. Один PR — одна задача.
- Контракт фронт ↔ бэк — только `docs/openapi.yaml`. Изменение API = отдельный PR с упоминанием фронтенда.
- Статусы и переходы заявки — строго по `docs/application-states.md`. Новый статус = сначала правка документа.
- Каждый переход статуса пишется в `application_events` (аудит).
- Логика скоринга и решения — чистые функции в `backend/app/decision/`, без обращений к БД и сети; на каждую синтетическую компанию есть тест.
- ML-модель — только challenger: не меняет исход заявки, видна только банку. Библиотеки обучения (scikit-learn и др.) живут в `ml/` и не попадают в образ backend; в рантайме — коэффициенты из JSON и чистый Python.
- Тексты интерфейса — на русском, простым языком, без банковского жаргона.
- Новая сторонняя библиотека, модель или датасет → строка в `THIRD_PARTY_NOTICES.md`.
- Секреты не коммитить; переменные окружения описывать в `.env.example`.

## Команды

Подробности и варианты — в разделе README «Проверки и тесты». Рабочий образ backend не содержит pytest и тестов, поэтому `docker compose run --rm backend pytest` не работает: тесты запускаются из исходников через uv.

```bash
cp .env.example .env                       # один раз, затем задать AUTH_JWT_SECRET
docker compose up --build --wait           # весь стенд: http://localhost:8080

cd backend
uv run --locked pytest -q                  # unit-тесты, включая decide() на всех синтетических компаниях
docker compose up -d db                    # для интеграционных тестов нужна PostgreSQL 16
uv run --locked --env-file ../.env pytest -q --run-postgres
uv run --locked ruff check . ../scripts
uv run --locked python -m app.export_openapi   # после изменения API: перегенерировать docs/openapi.yaml

cd ../frontend
pnpm install --frozen-lockfile && pnpm api:generate && pnpm test && pnpm build

python3 scripts/check_docker.py            # сквозная проверка на отдельном стенде
python3 api-collection/run_e2e.py          # все 9 компаний через API запущенного стенда
```

В `main` можно слить только PR с зелёными CI-проверками `Backend checks` и `Clean Docker product` (`.github/workflows/ci.yml`): CI запускает полный pytest с PostgreSQL и собирает чистый Docker-стенд.
