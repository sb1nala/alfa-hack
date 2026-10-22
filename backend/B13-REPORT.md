# B13: CI и техническая готовность

22.10.2026. **Заблокировано**: #76 (сборка исходного frontend) и SSE в рабочем UI
(#6). Ветка `feat/backend-ci` от свежего `main` `4b0d556`; PR будет указан после создания.
#82 фактически слит; B10 не принят. Исторические результаты B10 сохранены со своим commit.

## Что реализовано

- `Project CI`: pull_request в main, push в main, workflow_dispatch; независимые
  `Backend checks` и `Clean Docker product`, без path-фильтров и обходов #76.
- Python 3.12, uv 0.12.23, locked dependencies; Ruff, format вне app/decision,
  OpenAPI, полный pytest с PostgreSQL 16, контроль JUnit без skips и фиксированного количества.
- Исходный Compose build/up, штатный migrate/seed, nginx API runner, файлы/аудит,
  persistence, сбой БД, серверный EventSource и рабочий UI. `REQUIRE_UI_SSE=1`
  отклоняет UI, который не открывает `/events`; полная приёмка UI resilience отдельно.
- Случайные секреты, уникальные проекты/порты/volumes, таймауты и cancellation,
  адресная очистка в finally и always(), безопасные диагностические тексты 7 дней.
  Приватное состояние с credentials не входит в artifact paths.

QA `.github/workflows/qa-e2e.yml` из `qa/e2e-stand` прочитан (blob
`c00d5b0714e658cb4561cf7d23c6f6598b0fd98d`): переиспользованы подход чистого
Compose/продуктового runner, concurrency и отчётов. Не перенесены генерация типов,
необязательный browser flow, сырые логи и слабые проверки возврата БД. Бизнес-код,
frontend/mocks/seed/ml/decision, API и api-collection не изменены.

Actions закреплены по реальным upstream SHA, проверенным `git ls-remote`:
checkout v6 `d23441a`, setup-python v6 `ece7cb0`, setup-node v6 `2499707`,
upload-artifact v6 `b7c566a`, setup-uv v7 `94527f2`.

## Проверки реализации

- `actionlint` 1.7.12: PASS; release archive проверен upstream SHA256 checksums.
- Ruff, format в своей области, OpenAPI и Node syntax: PASS.
- CI safety tests: 7 passed (редакция credentials, сохранение exit code, отказ
  пустого/пропущенного/failed pytest, произвольное число тестов, адресная очистка).
- Первый полный PostgreSQL pytest: 548 passed / 1 failed, без skips (165,59 с).
  Существующий тест `test_recheck_access_after_slow_heartbeat_before_event`
  исчерпал send-timeout 0,3 с на независимый DB commit + намеренную задержку.
  Только для этого теста бюджет увеличен до 1,5 с: он проверяет access recheck,
  а timeout проверяется отдельными тестами. Продуктовые deadlines не изменены.
  Полный повтор с 7 новыми CI-тестами выполняется; результат будет внесён.
- Локальный строгий product runner: FAIL до frontend на HTTPS inspection CA
  (`UnknownIssuer` в uv внутри Docker). Это ограничение облачного build-окружения,
  не #76 и не успешная приёмка. Обёртка сохранила ненулевой код и выполнила очистку.
- GitHub Actions: результат будет проверен после создания PR; локальная валидация
  workflow не выдаётся за успешный Actions-прогон.

## Остаток для приёмки

Frontend: закоммитить актуальные API-типы и успешную штатную сборку (#76), подключить
EventSource в React, проверить именованные события, onopen/refetch, revision/id,
восстановление истории и CLOSED/502. После слияния повторить строгий product job.
Тимлид: проверить фактические Actions и включить `Backend checks`, `Clean Docker product`
как required checks защиты main после зелёного прогона. Настройки защиты не менялись,
Issues #6/#9/#76/#77 не закрывались, сообщения участникам не отправлялись.
