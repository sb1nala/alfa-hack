# B13: CI и техническая готовность

## Текущий статус после итоговой технической приёмки, 23.10.2026 (UTC)

**Реализация завершена и слита:** B10 [#82](https://github.com/sb1nala/alfa-hack/pull/82),
B13 [#84](https://github.com/sb1nala/alfa-hack/pull/84), frontend SSE [#85](https://github.com/sb1nala/alfa-hack/pull/85),
API review_reason [#89](https://github.com/sb1nala/alfa-hack/pull/89) по уже закрытому #88.
Проверенный свежий main: `1c93ac7c32e644667e99959109b90a04cf6403aa`.
[Project CI #15](https://github.com/sb1nala/alfa-hack/actions/runs/39926680775), push этого main:

- [Backend checks — success](https://github.com/sb1nala/alfa-hack/actions/runs/39926680775/job/120324682397),
  **576 passed**, без skips/failures/errors; Ruff/format/OpenAPI/миграции PASS.
- [Clean Docker product — success](https://github.com/sb1nala/alfa-hack/actions/runs/39926680775/job/120324682864),
  исходный Compose без обхода api:check, migrate/seed, API 10/10, PDF/аудит/тома,
  возврат БД, серверный SSE (heartbeat 15002 мс/Last-Event-ID/replay),
  React до ISSUED без reload, 1 EventSource и 11 GET карточки.
- Отдельная адресная проверка на том же commit: реальная остановка мока и БД
  во время обработки; прежние API/worker восстановились без requeue и дублей.

**Backend технически готов; общая QA-приёмка не завершена.** Восстановление самого
React UI при потере сети/CLOSED/502 остаётся frontend/QA, required checks — тимлиду.
Детальная матрица доказательств, ограничения, DoD #9 и рекомендации #6/#9/#77/#88:
[ACCEPTANCE-REPORT.md](ACCEPTANCE-REPORT.md). Настройки защиты не менялись.

## История до слияния B13 — исходные результаты сохранены

Все статусы «На ревью»/Draft и «слияние не выполнялось» ниже относятся к моменту
соответствующего прогона. Они не являются текущим статусом слитых PR.

22.10.2026 (Москва). **На ревью: повтор после исправления диагностики прошёл**, [PR #84](https://github.com/sb1nala/alfa-hack/pull/84).
Ветка `feat/backend-ci` создана от `4b0d556`; #83 устранил рассинхронизацию типов,
#85 реализовал SSE рабочего клиента. Изменения #85 включены в проверенный commit
`1b3732d234a2c825d444b6690dff068bb92c9ebf`; main после #85 — `d1de96c`.
На актуальном проверенном code commit `16525ca7bbb283fed6d58fd1bdba5895de25fb6c`
оба обязательных jobs [Actions 39843083544](https://github.com/sb1nala/alfa-hack/actions/runs/39843083544)
**прошли** после улучшения диагностики. Первый зелёный прогон после #85
на `1b3732d` сохранён ниже. Ранее зафиксированный блокер polling снят; критерии не ослаблялись.
#82 фактически слит. B10/B13 передаются на ревью; слияние #84 не выполнялось.

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
  а timeout проверяется отдельным тестом. Продуктовые deadlines не изменены.
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

## Исторический итог после merge #83, до #85

Проверен commit **`32a57441d815695b94ac081b077426d321d0fc3a`** на базе main
`e8d72ca`: [Actions run 39836676297](https://github.com/sb1nala/alfa-hack/actions/runs/39836676297).
Следующий commit обновляет только техническую документацию с этими результатами.

| Проверка в GitHub Actions | Результат |
| --- | --- |
| Backend checks | **PASS**, 556 passed / 1 прежнее warning, 187,61 с, без skips |
| Исходные образы и штатный up/migrate/seed | **PASS**, включая frontend после #83; обходов и ручной подготовки БД нет |
| run_e2e.py через nginx | **PASS 10/10**, все 9 компаний, причины, негативные HTTP, аналитик, клиентские действия и аудит |
| PDF bytes / NEEDS_INFO / issuance / точный аудит | **PASS** |
| Down/up с томами, сессией, карточкой, историей и файлом; повтор migrate/seed | **PASS** |
| Отказ/возврат PostgreSQL, ready=503/live=200 и сохранность | **PASS** |
| Серверный Chromium EventSource через nginx | **PASS**, heartbeat 15002 мс, native Last-Event-ID, onopen/refetch, restart/recreate/DNS, live и replay |
| Рабочий SPA через формы без reload | **PASS**, login → подбор → ИНН → submit → решение → accept → sign → ISSUED |
| SSE именно в рабочем React UI | **FAIL #6/TS-10**: 0 EventSource, 8 card GET; используется polling |
| Очистка, аудит и upload безопасной диагностики | **PASS** |

Итог `Clean Docker product` — **FAIL именно на отсутствии SSE в рабочем UI**.
#76 после #83 больше не является причиной красного CI. Ранние сообщения nginx
при намеренном restart/recreate ожидаемы; серверный тест подтвердил восстановление.
Тестовый EventSource не засчитывается за React. Браузерные resilience-критерии
самого UI ещё требуют frontend-реализации и отдельной приёмки.

Локальная сборка frontend на e8d72ca с адаптацией только CA также **PASS**:
api:check → TypeScript → Vite, готовый образ затем удалён. Это диагностика среды;
подтверждение чистого запуска получено именно в Actions выше.

## Исторический остаток после #83, до #85

Frontend: API-типы уже обновлены и слиты в #83 (сам #76 закрыт владельцем).
Остаётся подключить EventSource в React, проверить именованные события, onopen/refetch, revision/id,
восстановление истории и CLOSED/502. После слияния повторить строгий product job.
Тимлид: проверить фактические Actions и включить `Backend checks`, `Clean Docker product`
как required checks защиты main после зелёного прогона. Настройки защиты не менялись.
Автор B13 Issues не закрывал; #76 закрыт через frontend #83. #6/#9/#77 остаются
открыты, сообщения участникам не отправлялись.


## Актуальный результат после #85

[Actions 39838447107](https://github.com/sb1nala/alfa-hack/actions/runs/39838447107),
commit `1b3732d234a2c825d444b6690dff068bb92c9ebf`:

| Проверка | Результат |
| --- | --- |
| Backend checks | **PASS**, 556 passed, 1 прежнее warning, 145,98 с; JUnit без skips/failures/errors |
| Clean Docker product | **PASS**, штатные образы/up/migrate/seed, без ручной подготовки БД и обходов типов |
| API через nginx | **PASS 10/10**, 9 компаний и негативные проверки |
| PDF bytes, NEEDS_INFO, выдача, точный аудит | **PASS** |
| Down/up с томами, карточкой, историей, файлом и сессией; повтор seed | **PASS** |
| Отказ/возврат PostgreSQL, ready=503/live=200 | **PASS** |
| Тестовый Chromium EventSource | **PASS**, heartbeat 15002 мс, Last-Event-ID, onopen/refetch, restart/recreate/DNS, live и replay |
| Рабочий React UI | **PASS**, реальные формы до ISSUED без reload; 1 EventSource, 11 GET карточки; успешный text/event-stream |
| Адресная очистка | **PASS** в обоих jobs |

11 GET карточки включают действия и перечитывания после событий; наличие этих
запросов не свидетельствует о прежнем интервальном polling. Проверка
`REQUIRE_UI_SSE=1` и проверка успешного browser response оставлены без изменений.
Ошибок CI на этом commit нет; исправления workflow/scripts/backend не требуются.
Следующий commit меняет только четыре технических документа с этими результатами;
его автоматический прогон также будет проверен перед переводом PR в Ready for review.

Граница результата: основной UI-сценарий подтверждает реальное подключение SSE;
устойчивость при restart/recreate проверена отдельным тестовым EventSource.
Расширенная QA-приёмка восстановления именно React UI при потере сети/CLOSED/502
не включена в этот smoke и не объявляется выполненной. Это не оставшийся сбой CI
или отсутствие frontend-реализации: в #85 автор сообщил о 50 прошедших frontend
тестах, успешном pnpm build и браузерной проверке разрыва/возврата на синтетическом API.
Эти результаты не выдаются за повторный локальный прогон B13 или Docker-приёмку UI.

Тимлиду: ревью #84 и настройка required checks `Backend checks`, `Clean Docker product`.
Состояние B10/B13 — «На ревью», окончательную приёмку и merge выполняет команда.
Автор B13 Issues не закрывает и настройки защиты main не меняет.


## Продолжение: отмена backend job на повторе документации

На `12fe47a6eb7d450dac40206fc8ccff2a4109094d`
[Actions 39839522983](https://github.com/sb1nala/alfa-hack/actions/runs/39839522983):
`Clean Docker product` **PASS**, рабочий UI установил 1 EventSource, API 10/10.
`Backend checks` **cancelled** после почти 20 минут на шаге полного pytest;
в логе есть `The operation was canceled`, завершённого pytest/JUnit результата нет.
Очистка контейнера прошла. Это не считается успешным backend CI; PR возвращён в Draft.
Точная причина остановки pytest из прежнего лога не установлена.

Обнаружен дефект диагностического вывода CI: pytest целиком перенаправлялся в файл,
который печатался только после завершения. При отмене терялась информация о текущем
тесте. Вывод теперь фильтруется и сохраняется **построчно во время выполнения**;
добавлены имена тестов (`-vv`) и `faulthandler_timeout=60` (печатает traceback
долгого теста, не меняет исход теста и не является retry/skip).
Bash `pipefail` сохраняет ошибку pytest при успешной фильтрации вывода.
20-минутный job timeout, полный --run-postgres, JUnit gate и все критерии сохранены.
Локальные safety tests: **8 passed**, включая проверку ненулевого кода pipeline
и отсутствия credentials в console/artifact. Ruff и actionlint проходят.
После исправления диагностики полный локальный прогон: **557 passed, 1 прежнее
warning, 172,02 с**, JUnit без skips/failures/errors. Временный контейнер и БД удалены.

## Итог повтора после исправления диагностики

Проверенный code commit: `16525ca7bbb283fed6d58fd1bdba5895de25fb6c`.
[Actions 39843083544](https://github.com/sb1nala/alfa-hack/actions/runs/39843083544):

| Проверка | Факт |
| --- | --- |
| Backend checks | **PASS**, 557 passed, 1 прежнее warning, 210,87 с; JUnit без skips/failures/errors |
| Clean Docker product | **PASS** |
| Исходная сборка, up, штатные migrate/seed | **PASS**, без обхода api:check и ручной подготовки БД |
| API через nginx | **PASS 10/10** |
| PDF bytes, NEEDS_INFO, выдача, точный аудит | **PASS** |
| Down/up с томами/сессией/файлом; повтор seed; отказ/возврат БД | **PASS** |
| Серверный EventSource | **PASS**, heartbeat 15003 мс, native Last-Event-ID, restart/recreate/DNS, live и replay |
| Реальный React UI | **PASS**, до ISSUED без reload, 1 EventSource с text/event-stream, 10 GET карточки |
| Адресная очистка обоих jobs | **PASS** |

Отмена предыдущего прогона не повторилась. Её первопричину старый лог не позволяет
установить; изменение исправляет наблюдаемость CI, а не объявляет исправленным
неподтверждённый дефект продукта. Полный pytest, запрет skips, --locked,
REQUIRE_UI_SSE=1 и job timeout 20/35 минут сохранены.

B10/B13 — **На ревью**. Следующий commit обновляет только технические документы;
его собственный автоматический CI проверяется перед снятием Draft. Окончательная
QA-приёмка устойчивости React UI сохраняет описанную выше границу smoke.
Слияние #84, закрытие Issues и настройка branch protection не выполнялись.
