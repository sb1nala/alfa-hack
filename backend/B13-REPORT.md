# B13: CI и техническая готовность

22.10.2026. **Заблокировано**: SSE в рабочем UI (#6/TS-10) и строгая приёмка
после исправления frontend. Ветка `feat/backend-ci` создана от `4b0d556`;
при финальном fetch обнаружен merge #83, main `e8d72ca`, #76 закрыт.
Этот main безопасно включён обычным merge, без изменения frontend со стороны B13;
[draft PR #84](https://github.com/sb1nala/alfa-hack/pull/84), слияние не выполнялось.
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
  Полный повтор с 7 новыми CI-тестами: **556 passed, 1 warning, 167,44 с**,
  без skips/failures/errors (подтверждено JUnit gate), включая риски и миграции.
  Предупреждение Starlette/TestClient/httpx существовало до B13.
- Локальный строгий product runner: FAIL до frontend на HTTPS inspection CA
  (`UnknownIssuer` в uv внутри Docker). Это ограничение облачного build-окружения,
  не #76 и не успешная приёмка. Обёртка сохранила ненулевой код и выполнила очистку.
- Локальная отдельная сборка исходного frontend с адаптацией **только доверия CA**
  (BuildKit secret + NODE_EXTRA_CA_CERTS в pnpm install) дошла до **FAIL #76**:
  `Generated types are not up-to-date!`. Исходные типы и api:check не менялись.
  Это диагностика ограничения среды, не чистый успешный запуск.
- Реальный [Actions run 39835941309](https://github.com/sb1nala/alfa-hack/actions/runs/39835941309)
  на `bf785d4`: **Clean Docker product — FAIL #76**, `pnpm api:check` /
  `Generated types are not up-to-date!`. Browser tools (Playwright + Chromium),
  адресная очистка, аудит логов и upload безопасного артефакта — PASS.
  API/persistence/browser после ошибки сборки не запускались.
  **Backend checks — PASS**: Ruff, format, OpenAPI, полный pytest, JUnit без
  skips и очистка тестового контейнера. Это фактический Actions, а не YAML-валидация.
  После усиления проверки browser response и маскирования демонстрационных
  паролей в текстовом pytest-отчёте выполнены целевые safety tests; новый push
  автоматически проверит итоговый commit. Ручные повторы дорогих jobs не запускались.

Повтор после merge #83: новый строгий Actions product job запущен без обходов;
результат будет проверен перед финальным отчётом. Предыдущий красный #76 выше
относится только к `bf785d4`, а не к обновлённому main.

## Остаток для приёмки

Frontend: API-типы уже обновлены и слиты в #83 (сам #76 закрыт владельцем).
Остаётся подключить EventSource в React, проверить именованные события, onopen/refetch, revision/id,
восстановление истории и CLOSED/502. После слияния повторить строгий product job.
Тимлид: проверить фактические Actions и включить `Backend checks`, `Clean Docker product`
как required checks защиты main после зелёного прогона. Настройки защиты не менялись,
Автор B13 Issues не закрывал; #76 закрыт через frontend #83. #6/#9/#77 остаются
открыты, сообщения участникам не отправлялись.
