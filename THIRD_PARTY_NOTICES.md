# Third-party notices

Сторонние компоненты прототипа, их лицензии и замоканные внешние интеграции. Все компоненты бесплатные: платных API, подписок и лицензий нет. Точные версии и хеши, включая транзитивные зависимости, зафиксированы в `backend/uv.lock`, `mocks/uv.lock` и `frontend/pnpm-lock.yaml`.

Список собран 24.10.2026. Пакеты рабочих образов взяты из метаданных установленных пакетов в контейнерах `backend` и `mocks` (`importlib.metadata`), dev-зависимости — из `uv.lock`, фронтенд — из `pnpm list` и `package.json` установленных пакетов. Лицензии указаны так, как их объявляют сами пакеты.

## Замоканные интеграции

Реальные внешние сервисы не вызываются. Сервис `mocks/` отдаёт синтетические ответы из [`seed/companies.json`](seed/companies.json). ИНН компаний начинаются с несуществующего кода региона `00`, названия и люди вымышлены.

| Реальный источник | Что имитирует мок | Эндпоинт мока | Ограничения |
| --- | --- | --- | --- |
| Бюро кредитных историй (БКИ), 218-ФЗ «О кредитных историях» | Действующие кредиты и платежи, просрочки за 24 месяца, запросы за 30 дней | `GET /bki/report?inn=` | Упрощённый формат, не формат реального бюро. Для `demo-bki` первые 2 ответа — `503` (демонстрация повторов) |
| ЕГРЮЛ / ЕГРИП (ФНС России) | Статус, дата регистрации, ОКВЭД, адрес, руководитель | `GET /egrul/{inn}` | Только поля для анкеты и стоп-факторов |
| ФНС «Прозрачный бизнес» | Выручка и расходы, численность, налоговый долг, массовый адрес, дисквалификация руководителя | `GET /pb/{inn}` | Только поля для скоринга |

Электронная подпись и выдача денег не интегрированы: подписание и зачисление симулируются внутри бэкенда.

## Сводка

| Где | Пакетов | Лицензии |
| --- | --- | --- |
| Рабочий образ backend и worker | 32 | MIT, BSD-3-Clause, Apache-2.0, MIT-0, PSF-2.0, MPL-2.0 (только certifi) |
| Рабочий образ mocks | 14 | MIT, BSD-3-Clause, Apache-2.0, PSF-2.0 |
| Браузерный бандл фронтенда | 38 | MIT; BSD-3-Clause (только react-transition-group) |
| Инструменты разработки и проверки | — | MIT, Apache-2.0, BSD-2/3-Clause, ISC и другие разрешительные (ниже); в образы не попадают |

Лицензий с сильным копилефтом (GPL, AGPL) нет. certifi распространяется по MPL-2.0 (слабый копилефт на уровне файлов) и используется без изменений как набор корневых сертификатов.

## Платформа и Docker-образы

| Компонент | Версия | Лицензия | Использование |
| --- | --- | --- | --- |
| [PostgreSQL](https://www.postgresql.org/) (`postgres:16-bookworm`) | 16 | PostgreSQL License | База данных стенда |
| [Python](https://www.python.org/) (`python:3.12-slim-bookworm`) | 3.12 | PSF-2.0 | Среда образов backend, worker, migrate и mocks |
| [nginx](https://nginx.org/) (`nginxinc/nginx-unprivileged:1.27.5-alpine`) | 1.27.5 | BSD-2-Clause | Раздача SPA и прокси `/api` в образе фронтенда |
| [uv](https://github.com/astral-sh/uv) (`ghcr.io/astral-sh/uv:0.12.23`) | 0.12.23 | MIT OR Apache-2.0 | Только этап сборки: установка зависимостей по `uv.lock` |
| [Node.js](https://nodejs.org/) (`node:24.19.0-bookworm-slim`) | 24.19.0 | MIT | Только этап сборки фронтенда |

Системные пакеты базовых образов Debian и Alpine распространяются по своим лицензиям.

## Бэкенд и worker — рабочий образ

Один образ для `backend`, `worker` и `migrate`.

| Компонент | Версия | Лицензия | Использование |
| --- | --- | --- | --- |
| [FastAPI](https://pypi.org/project/FastAPI/) | 0.155.0 | MIT | HTTP API, OpenAPI, SSE |
| [Starlette](https://pypi.org/project/Starlette/) | 1.9.0 | BSD-3-Clause | ASGI-основа FastAPI |
| [Uvicorn](https://pypi.org/project/Uvicorn/) | 0.54.0 | BSD-3-Clause | ASGI-сервер |
| [Pydantic](https://pypi.org/project/Pydantic/) | 2.14.1 | MIT | Схемы API и валидация |
| [pydantic-settings](https://pypi.org/project/pydantic-settings/) | 2.16.1 | MIT | Настройки из переменных окружения |
| [SQLAlchemy](https://pypi.org/project/SQLAlchemy/) | 2.0.55 | MIT | ORM и запросы к PostgreSQL (async) |
| [asyncpg](https://pypi.org/project/asyncpg/) | 0.32.0 | Apache-2.0 | Драйвер PostgreSQL |
| [Alembic](https://pypi.org/project/Alembic/) | 1.20.0 | MIT | Миграции схемы БД |
| [AnyIO](https://pypi.org/project/AnyIO/) | 4.15.1 | MIT | Защищённая от отмены очистка коротких SQL-операций (SSE) |
| [HTTPX](https://pypi.org/project/HTTPX/) | 0.28.1 | BSD-3-Clause | Запросы worker и поиска по ИНН к локальным мокам |
| [PyJWT](https://pypi.org/project/PyJWT/) | 2.15.1 | MIT | Подпись и проверка JWT (HS256) |
| [argon2-cffi](https://pypi.org/project/argon2-cffi/) | 25.1.0 | MIT | Хеширование паролей Argon2id |
| [python-multipart](https://pypi.org/project/python-multipart/) | 0.0.32 | Apache-2.0 | Загрузка документов (multipart) |
| [PyYAML](https://pypi.org/project/PyYAML/) | 6.0.3 | MIT | Экспорт `docs/openapi.yaml` |

Транзитивные зависимости в образе: annotated-doc 0.0.5 (MIT), annotated-types 0.8.0 (MIT), argon2-cffi-bindings 26.1.0 (MIT; включённая реализация Argon2 — Apache-2.0 / CC0), certifi 2026.10.19 (MPL-2.0), cffi 2.1.1 (MIT-0), click 8.5.0 (BSD-3-Clause), greenlet 3.5.6 (MIT AND PSF-2.0), h11 0.16.0 (MIT), httpcore 1.0.9 (BSD-3-Clause), idna 3.20 (BSD-3-Clause), Mako 1.4.3 (MIT), MarkupSafe 3.0.4 (BSD-3-Clause), opentelemetry-api 1.45.1 (Apache-2.0), pycparser 3.11 (BSD-3-Clause), pydantic_core 2.50.1 (MIT), python-dotenv 1.2.4 (BSD-3-Clause), typing-inspection 0.4.4 (MIT), typing_extensions 4.16.0 (PSF-2.0).

## Моки — рабочий образ

| Компонент | Версия | Лицензия | Использование |
| --- | --- | --- | --- |
| [FastAPI](https://pypi.org/project/FastAPI/) | 0.156.7 | MIT | HTTP API моков |
| [Starlette](https://pypi.org/project/Starlette/) | 1.9.0 | BSD-3-Clause | ASGI-основа FastAPI |
| [Uvicorn](https://pypi.org/project/Uvicorn/) | 0.54.0 | BSD-3-Clause | ASGI-сервер |
| [Pydantic](https://pypi.org/project/Pydantic/) | 2.14.1 | MIT | Валидация параметров |

Транзитивные зависимости в образе: annotated-doc 0.0.5 (MIT), annotated-types 0.8.0 (MIT), anyio 4.15.1 (MIT), click 8.5.0 (BSD-3-Clause), h11 0.16.0 (MIT), idna 3.20 (BSD-3-Clause), opentelemetry-api 1.45.1 (Apache-2.0), pydantic_core 2.50.1 (MIT), typing-inspection 0.4.4 (MIT), typing_extensions 4.16.0 (PSF-2.0).

## Фронтенд — браузерный бандл

Прямые зависимости ниже. Ещё 30 транзитивных пакетов бандла (части Emotion и MUI, Popper, React Router, clsx и др.) распространяются по MIT, кроме react-transition-group 4.4.5 (BSD-3-Clause). Шрифты, иконки и изображения сторонних авторов не подключаются; палитра интерфейса (`docs/product/design-tokens.md`) собственная.

| Компонент | Версия | Лицензия | Использование |
| --- | --- | --- | --- |
| [react](https://www.npmjs.com/package/react) / [react-dom](https://www.npmjs.com/package/react-dom) | 18.3.1 | MIT | Интерфейс и рендеринг |
| [react-router-dom](https://www.npmjs.com/package/react-router-dom) | 6.30.6 | MIT | Маршруты кабинетов клиента и банка |
| [@tanstack/react-query](https://www.npmjs.com/package/@tanstack/react-query) | 5.104.5 | MIT | Состояние серверных запросов |
| [@mui/material](https://www.npmjs.com/package/@mui/material) | 9.5.0 | MIT | Компоненты и тема интерфейса |
| [@emotion/react](https://www.npmjs.com/package/@emotion/react) / [@emotion/styled](https://www.npmjs.com/package/@emotion/styled) | 11.14.0 / 11.14.1 | MIT | Стили MUI |
| [openapi-fetch](https://www.npmjs.com/package/openapi-fetch) | 0.17.0 | MIT | Типизированные запросы по `docs/openapi.yaml` |

## Инструменты разработки и проверки

В рабочие образы и браузерный бандл не попадают.

**Python (backend, mocks)**

| Компонент | Версия | Лицензия | Использование |
| --- | --- | --- | --- |
| [pytest](https://pypi.org/project/pytest/) | 9.1.1 | MIT | Тесты backend и mocks |
| [Ruff](https://pypi.org/project/Ruff/) | 0.17.1 | MIT | Линтер и форматирование |
| [jsonschema](https://pypi.org/project/jsonschema/) | 4.26.0 | MIT | Проверка примеров контракта |
| [openapi-spec-validator](https://pypi.org/project/openapi-spec-validator/) | 0.7.2 | Apache-2.0 | Проверка `docs/openapi.yaml` |
| [rfc3339-validator](https://pypi.org/project/rfc3339-validator/) | 0.1.4 | MIT | Форматы дат в схемах |
| [HTTPX](https://pypi.org/project/HTTPX/) | 0.28.1 | BSD-3-Clause | Тестовый клиент mocks |

Транзитивные: attrs 26.1.0 (MIT), charset-normalizer 3.5.2 (MIT), colorama 0.4.6 (BSD-3-Clause), iniconfig 2.3.1 (MIT), jsonschema-path 0.3.4 (Apache-2.0), jsonschema-specifications 2025.9.1 (MIT), lazy-object-proxy 1.13.0 (BSD-2-Clause), openapi-schema-validator 0.6.3 (BSD-3-Clause), packaging 26.3 (Apache-2.0 OR BSD-2-Clause), pathable 0.4.4 (Apache-2.0), pluggy 1.7.0 (MIT), pygments 2.21.0 (BSD-2-Clause), referencing 0.36.2 (MIT), requests 2.34.2 (Apache-2.0), rpds-py 2026.9.1 (MIT), six 1.17.0 (MIT), urllib3 2.8.0 (MIT).

**Фронтенд**

| Компонент | Версия | Лицензия | Использование |
| --- | --- | --- | --- |
| [vite](https://www.npmjs.com/package/vite) | 8.3.5 | MIT | Сборка и сервер разработки |
| [@vitejs/plugin-react](https://www.npmjs.com/package/@vitejs/plugin-react) | 6.1.2 | MIT | React для Vite |
| [typescript](https://www.npmjs.com/package/typescript) | 5.9.3 | Apache-2.0 | Проверка типов |
| [openapi-typescript](https://www.npmjs.com/package/openapi-typescript) | 7.13.0 | MIT | Генерация типов из `docs/openapi.yaml` |
| [@stoplight/prism-cli](https://www.npmjs.com/package/@stoplight/prism-cli) | 5.16.0 | Apache-2.0 | Mock-сервер по контракту для проверки запросов фронтенда |
| [@types/react](https://www.npmjs.com/package/@types/react), [@types/react-dom](https://www.npmjs.com/package/@types/react-dom), [@types/node](https://www.npmjs.com/package/@types/node) | 18.3.31, 18.3.7, 24.10.1 | MIT | Типы |
| [pnpm](https://www.npmjs.com/package/pnpm) | 11.25.0 | MIT | Менеджер пакетов (`packageManager`) |

Всего 146 dev-пакетов: MIT — 116, Apache-2.0 — 14 (TypeScript, пакеты Prism `@stoplight/*`, `@scarf/scarf`, `yaml-ast-parser`), ISC — 12, по одному BSD-3-Clause (`qs`), 0BSD (`tslib`), Python-2.0 (`argparse`) и MIT OR CC0-1.0 (`type-fest`). Установочный скрипт `@scarf/scarf` отключён, телеметрия при установке не запускается.

**CI, QA и документация**

| Компонент | Версия | Лицензия | Использование |
| --- | --- | --- | --- |
| [Playwright](https://playwright.dev/) + Chromium | 1.57.0 | Apache-2.0 (Chromium — BSD-3-Clause и лицензии включённых компонентов) | Браузерная проверка в CI; ставится во временный каталог раннера |
| [Bruno](https://www.usebruno.com/) / [@usebruno/cli](https://www.npmjs.com/package/@usebruno/cli) | актуальная | MIT | Коллекция запросов `api-collection/`, ставится отдельно |
| [Mermaid](https://mermaid.js.org/) | — | MIT | Диаграммы в Markdown, рендерит GitHub |
| GitHub Actions: `actions/setup-python`, `actions/setup-node`, `astral-sh/setup-uv` | v6, v6, v7 | MIT | CI; версии закреплены по SHA в `.github/workflows/ci.yml` |

Скрипты `api-collection/generate.py`, `api-collection/run_e2e.py`, `scripts/*.py` и `docs/product/render_ui_texts.py` используют только стандартную библиотеку Python.

## Данные и модели

Все данные синтетические (`seed/`), сторонние датасеты не используются. ML- и LLM-моделей в прототипе нет, внешние ИИ-сервисы не вызываются (см. [архитектуру](docs/architecture.md#ии-и-ml-развитие)).
