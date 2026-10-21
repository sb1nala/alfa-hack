# Third-party notices

Список сторонних библиотек и замоканных интеграций будет дополняться по мере разработки.

## Замоканные интеграции

- Бюро кредитных историй (БКИ): локальный мок, реальные данные не используются.
- ЕГРЮЛ / ЕГРИП: локальный мок.
- ФНС «Прозрачный бизнес»: локальный мок.

## Контракт API и проверки (задача 2)

Новые зависимости и их транзитивные зависимости; точные версии и хеши —
`backend/uv.lock`. Dev-зависимости не входят в runtime Docker-образ.

| Библиотека | Версия | Лицензия | Использование |
| --- | --- | --- | --- |
| [attrs](https://pypi.org/project/attrs/) | 26.1.0 | MIT | Dev: структурная проверка OpenAPI/JSON Schema и зависимостей |
| [charset-normalizer](https://pypi.org/project/charset-normalizer/) | 3.5.2 | MIT | Dev: структурная проверка OpenAPI/JSON Schema и зависимостей |
| [jsonschema](https://pypi.org/project/jsonschema/) | 4.26.0 | MIT | Dev: структурная проверка OpenAPI/JSON Schema и зависимостей |
| [jsonschema-path](https://pypi.org/project/jsonschema-path/) | 0.3.4 | Apache-2.0 | Dev: структурная проверка OpenAPI/JSON Schema и зависимостей |
| [jsonschema-specifications](https://pypi.org/project/jsonschema-specifications/) | 2025.9.1 | MIT | Dev: структурная проверка OpenAPI/JSON Schema и зависимостей |
| [lazy-object-proxy](https://pypi.org/project/lazy-object-proxy/) | 1.13.0 | BSD-2-Clause | Dev: структурная проверка OpenAPI/JSON Schema и зависимостей |
| [openapi-schema-validator](https://pypi.org/project/openapi-schema-validator/) | 0.6.3 | BSD-3-Clause | Dev: структурная проверка OpenAPI/JSON Schema и зависимостей |
| [openapi-spec-validator](https://pypi.org/project/openapi-spec-validator/) | 0.7.2 | Apache-2.0 | Dev: структурная проверка OpenAPI/JSON Schema и зависимостей |
| [pathable](https://pypi.org/project/pathable/) | 0.4.4 | Apache-2.0 | Dev: структурная проверка OpenAPI/JSON Schema и зависимостей |
| [python-multipart](https://pypi.org/project/python-multipart/) | 0.0.32 | Apache-2.0 | Разбор multipart-запросов заглушки |
| [pyyaml](https://pypi.org/project/pyyaml/) | 6.0.3 | MIT | Экспорт YAML |
| [referencing](https://pypi.org/project/referencing/) | 0.36.2 | MIT | Dev: структурная проверка OpenAPI/JSON Schema и зависимостей |
| [requests](https://pypi.org/project/requests/) | 2.34.2 | Apache-2.0 | Dev: структурная проверка OpenAPI/JSON Schema и зависимостей |
| [rfc3339-validator](https://pypi.org/project/rfc3339-validator/) | 0.1.4 | MIT | Dev: структурная проверка OpenAPI/JSON Schema и зависимостей |
| [rpds-py](https://pypi.org/project/rpds-py/) | 2026.9.1 | MIT | Dev: структурная проверка OpenAPI/JSON Schema и зависимостей |
| [six](https://pypi.org/project/six/) | 1.17.0 | MIT | Dev: структурная проверка OpenAPI/JSON Schema и зависимостей |
| [urllib3](https://pypi.org/project/urllib3/) | 2.8.0 | MIT | Dev: структурная проверка OpenAPI/JSON Schema и зависимостей |

Pydantic (MIT) уже присутствовал транзитивно в задаче 1 и теперь также объявлен
прямой зависимостью для схем. Его версия зафиксирована в том же lock-файле.

Временная проверка генерации типов: `openapi-typescript` 7.10.1 (MIT) и
TypeScript 5.9.3 (Apache-2.0), запускаются через npm во временном каталоге;
в зависимости приложения и frontend не добавлены.
Prism CLI 5.16.0 (Apache-2.0) также использован только для временного smoke-теста
именованных примеров контракта.

## Миграции БД (B03)

Runtime-зависимость и новые транзитивные зависимости; версии и хеши зафиксированы
в `backend/uv.lock`.

| Библиотека | Версия | Лицензия | Использование |
| --- | --- | --- | --- |
| [Alembic](https://pypi.org/project/alembic/) | 1.20.0 | MIT | Миграции схемы SQLAlchemy/PostgreSQL |
| [Mako](https://pypi.org/project/Mako/) | 1.4.3 | MIT | Шаблон генерации миграций Alembic |
| [MarkupSafe](https://pypi.org/project/MarkupSafe/) | 3.0.4 | BSD-3-Clause | Экранирование в Mako |

## Моки внешних сервисов (#19)

Отдельный сервис `mocks/`; версии и хеши зафиксированы в `mocks/uv.lock`. FastAPI, Starlette, Pydantic и Uvicorn — те же библиотеки, что у бэкенда, без новых лицензий.

| Библиотека | Версия | Лицензия | Использование |
| --- | --- | --- | --- |
| [FastAPI](https://pypi.org/project/fastapi/) | 0.156.7 | MIT | HTTP API моков |
| [Starlette](https://pypi.org/project/starlette/) | 1.9.0 | BSD-3-Clause | ASGI-основа FastAPI |
| [Pydantic](https://pypi.org/project/pydantic/) | 2.14.1 | MIT | Валидация параметров |
| [Uvicorn](https://pypi.org/project/uvicorn/) | 0.54.0 | BSD-3-Clause | ASGI-сервер |
| [HTTPX](https://pypi.org/project/httpx/) | 0.28.1 | BSD-3-Clause | Dev: тестовый клиент |
| [pytest](https://pypi.org/project/pytest/) | 9.1.1 | MIT | Dev: тесты |

Данные моков синтетические (`seed/companies.json`): ИНН с несуществующим кодом региона `00`, названия и люди вымышлены.


## Авторизация (B04)

Версии и хеши зафиксированы в `backend/uv.lock`; все библиотеки бесплатные.

| Библиотека | Версия | Лицензия | Использование |
| --- | --- | --- | --- |
| [argon2-cffi](https://pypi.org/project/argon2-cffi/) | 25.1.0 | MIT | Argon2id для хеширования и проверки паролей |
| [argon2-cffi-bindings](https://pypi.org/project/argon2-cffi-bindings/) | 26.1.0 | MIT (включённый Argon2: Apache-2.0 / CC0) | Поддерживаемые bindings к реализации Argon2 |
| [cffi](https://pypi.org/project/cffi/) | 2.1.1 | MIT | Транзитивная зависимость bindings |
| [pycparser](https://pypi.org/project/pycparser/) | 3.11 | BSD-3-Clause | Транзитивная зависимость cffi |
| [PyJWT](https://pypi.org/project/PyJWT/) | 2.15.1 | MIT | Подпись и проверка JWT, фиксированный HS256 |

## Каталог и черновики (B05)

HTTPX перенесён также в runtime бэкенда для обращения только к локальному моку
ЕГРЮЛ. Версии и хеши — `backend/uv.lock`; внешних сервисов и датасетов не добавлено.

| Библиотека | Версия | Лицензия | Использование |
| --- | --- | --- | --- |
| [HTTPX](https://pypi.org/project/httpx/) | 0.28.1 | BSD-3-Clause | Async HTTP, таймаут запросов к моку |
| [httpcore](https://pypi.org/project/httpcore/) | 1.0.9 | BSD-3-Clause | Транспорт HTTPX |
| [h11](https://pypi.org/project/h11/) | 0.16.0 | MIT | HTTP/1.1 для httpcore |
| [certifi](https://pypi.org/project/certifi/) | 2026.10.19 | MPL-2.0 | Корневые CA для HTTPX/httpcore |

## Каркас фронтенда (#11)

Прямые зависимости перечислены ниже; точные версии транзитивных зависимостей
и integrity-хеши — `frontend/pnpm-lock.yaml`. Все компоненты бесплатные;
шрифты, изображения и внешние сервисы не подключаются.

| Библиотека | Версия | Лицензия | Использование |
| --- | --- | --- | --- |
| [React](https://www.npmjs.com/package/react) / [React DOM](https://www.npmjs.com/package/react-dom) | 18.3.1 | MIT | Интерфейс и DOM-рендеринг |
| [React Router DOM](https://www.npmjs.com/package/react-router-dom) | 6.30.6 | MIT | Маршруты клиентского и банковского разделов |
| [TanStack React Query](https://www.npmjs.com/package/@tanstack/react-query) | 5.104.5 | MIT | Провайдер состояния серверных запросов |
| [MUI Material](https://www.npmjs.com/package/@mui/material) | 9.5.0 | MIT | Компоненты и тема интерфейса |
| [Emotion React](https://www.npmjs.com/package/@emotion/react) | 11.14.0 | MIT | Стили MUI |
| [Emotion Styled](https://www.npmjs.com/package/@emotion/styled) | 11.14.1 | MIT | Стили MUI |
| [Vite](https://www.npmjs.com/package/vite) | 8.3.5 | MIT | Dev: сервер разработки и сборка |
| [Vite React plugin](https://www.npmjs.com/package/@vitejs/plugin-react) | 6.1.2 | MIT | Dev: React Fast Refresh |
| [TypeScript](https://www.npmjs.com/package/typescript) | 5.9.3 | Apache-2.0 | Dev: проверка типов |
| [@types/react](https://www.npmjs.com/package/@types/react) | 18.3.31 | MIT | Dev: типы React |
| [@types/react-dom](https://www.npmjs.com/package/@types/react-dom) | 18.3.7 | MIT | Dev: типы React DOM |
| [@types/node](https://www.npmjs.com/package/@types/node) | 24.10.1 | MIT | Dev: типы Node.js для конфигурации Vite |
| [pnpm](https://www.npmjs.com/package/pnpm) | 11.25.0 | MIT | Менеджер пакетов, версия в packageManager |

## Типизированный API-клиент (#12)

| Библиотека | Версия | Лицензия | Использование |
| --- | --- | --- | --- |
| [openapi-fetch](https://www.npmjs.com/package/openapi-fetch) | 0.17.0 | MIT | Типизированные HTTP-запросы по контракту |
| [openapi-typescript](https://www.npmjs.com/package/openapi-typescript) | 7.13.0 | MIT | Dev: генерация TypeScript-типов из OpenAPI |
| [Prism CLI](https://www.npmjs.com/package/@stoplight/prism-cli) | 5.16.0 | Apache-2.0 | Dev: локальный mock-сервер OpenAPI и HTTP smoke-проверка |

Версии и integrity-хеши, включая транзитивные зависимости, зафиксированы в
`frontend/pnpm-lock.yaml`. Генератор и Prism не входят в браузерный bundle.
Тесты клиента используют встроенный Node.js test runner (без новой библиотеки).
Транзитивный установочный скрипт `@scarf/scarf` явно отключён, телеметрия при
установке не запускается. Моковые ответы и аккаунты — только синтетические примеры
из существующего OpenAPI; новые датасеты и внешние сервисы не добавляются.
