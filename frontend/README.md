# Фронтенд кредитного кабинета

Каркас по [задаче #11](https://github.com/sb1nala/alfa-hack/issues/11): Vite,
React 18, TypeScript в strict-режиме, React Router, TanStack Query и MUI.
Заглушки работают без backend и без тестовых аккаунтов.

## Запуск

Node.js 24 (проверено на 24.19.0, версия в `.node-version`), pnpm 11.25.0
(версия в `package.json`). Из корня репозитория:

```sh
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

Откройте http://127.0.0.1:5173/client и http://127.0.0.1:5173/bank.
Корневой адрес перенаправляет на `/client`; неизвестный адрес показывает 404.
Переключение разделов и прямое открытие/обновление страницы работают через Router.
Занятый порт 5173 вызывает явную ошибку вместо запуска на другом порту.

```sh
pnpm typecheck
pnpm build
pnpm preview
```

`build` проверяет типы и создаёт `dist/`. Preview доступен на порту 4173.
Установка воспроизводится по `pnpm-lock.yaml`; `node_modules/` и `dist/` игнорируются.

## Согласованные требования коллег

| Источник | Что учтено в #11 |
| --- | --- |
| `docs/team-plan.md`, стартовый промпт фронтенда | React 18, Vite, TypeScript, pnpm, Router, Query, MUI, `/client` и `/bank` |
| `docs/product/design-tokens.md` | Светлая тема; бордо `#800020`, тиффани `#0ABAB5`, фон `#F7F3FA`, белые карточки, радиус 8; полная MUI-палитра в `src/theme.ts` |
| `docs/product/screen-map.md` | Разделы предпринимателя и кредитного аналитика; структура расширяется следующими задачами |
| `docs/product/ui-texts.ru.json` | Источник текстов будущих бизнес-экранов; технические заглушки пока используют собственные русские подписи |
| `AGENTS.md`, технические требования кейса | Бесплатные зависимости, фиксированные версии, локальный запуск, воспроизводимая установка |

Бордо используется для бренда и действий, не для ошибок. На тиффани — тёмный
текст, на белом ссылки имеют цвет `#00716E`. Статусы в будущих экранах должны
различаться подписью/иконкой, а не только цветом.

## Структура и границы

- `src/main.tsx`: единственный QueryClient, QueryClientProvider, ThemeProvider,
  CssBaseline, BrowserRouter и React StrictMode.
- `src/app/App.tsx`: маршруты.
- `src/components/`: общая оболочка и заглушка раздела.
- `src/pages/`: заглушки клиента, банка и 404.
- `src/theme.ts`: утверждённая продуктом тема MUI.

В dev Vite проксирует `/api` на `API_PROXY_TARGET` (по умолчанию
`http://127.0.0.1:8000`), сохраняя единый origin для cookie и будущего SSE.
Бэкенд или Prism запускается отдельно; заглушки страниц API не вызывают.
Переменная описана в `.env.example` и доступна только Vite, секреты не нужны.

Каркас не реализует авторизацию и разграничение ролей: переключатель разделов
служит для просмотра заглушек. #12 добавляет типы OpenAPI и API-клиент, описанные ниже.
В следующих задачах: #13 — вход и клиентские экраны с CSRF, #14 — решения, #15 — Docker/nginx,
#44 — кабинет аналитика. JWT только в HttpOnly cookie; CSRF-токен — в памяти.
Контрактом остаётся `docs/openapi.yaml`, кнопки действий определяет `allowed_actions`.

## Ручная проверка

1. Запустить `pnpm dev`, открыть оба маршрута напрямую и обновить страницу.
2. Переключить разделы, проверить Back/Forward и активный пункт навигации.
3. Открыть неизвестный путь, вернуться в кабинет кнопкой на странице 404.
4. Проверить ширину 375 px и навигацию клавишей Tab, включая ссылку пропуска шапки.
5. Выполнить `pnpm build`; повторить проверку обоих маршрутов через `pnpm preview`.

## Типизированный API-клиент (#12)

`src/api/schema.d.ts` генерируется **только** из `../docs/openapi.yaml`. Вручную
его не редактируем; суммы остаются строками, статусы и роли — union-типами,
параметры пути/запроса и тела — типами из контракта.

```sh
pnpm api:generate  # после обновления OpenAPI
pnpm api:check     # ненулевой код, если сгенерированные типы устарели
pnpm typecheck    # в том числе негативные компиляционные проверки контракта
pnpm test         # Node.js test runner, отдельная библиотека тестов не нужна
pnpm test:prism   # изолированный Prism + Vite + реальный HTTP-прогон
pnpm build        # включает api:check и typecheck
```

### Переключение Prism ↔ backend одной переменной

Создайте `frontend/.env` по `.env.example`. Меняется только `API_PROXY_TARGET`:

| Сервис | Значение |
| --- | --- |
| Настоящий backend | `http://127.0.0.1:8000` (по умолчанию) |
| Prism | `http://127.0.0.1:4010` |

После изменения перезапустите `pnpm dev`. Переменная не содержит `/api`, учётных
данных или секретов, не имеет префикса `VITE_` и не попадает в браузерный bundle.
Браузер всегда обращается к относительным путям `/api/...` своего origin через
экспортированный `api` из `src/api/index.ts`.

Для работы с Prism:

```sh
# Терминал 1, frontend/
pnpm mock:api
# Терминал 2, frontend/.env содержит API_PROXY_TARGET=http://127.0.0.1:4010
pnpm dev
```

Prism возвращает статические примеры OpenAPI: подходит для разработки запросов
и экранов, но не сохраняет заявки, не реализует настоящий вход, переходы статусов
или SSE. Не следует принимать пример ответа за результат бизнес-процесса.
`pnpm test:prism` поднимает собственные серверы на свободных портах и завершает
их после проверки. Для моделирования cookie в Node используются только
синтетические значения — это не проверка настоящей авторизации.

При работе с настоящим API бэкенду требуется
`AUTH_ALLOWED_ORIGIN=http://127.0.0.1:5173` для приведённого адреса dev-сервера.
Origin включает порт и должен совпадать **точно**. Настройку применяет владелец
backend и перезапускает его. Для локального HTTP также требуется
`AUTH_COOKIE_SECURE=false`. `changeOrigin: false` сохраняет Origin браузера;
CORS и ручной заголовок Authorization не нужны.

Dev-прокси относится к `pnpm dev` и к локальному preview Vite. В production
адрес задаст nginx в задаче #15; менять адрес API в исходниках клиента не нужно.

### Использование

```ts
import { api, ApiError } from './api';

const { data } = await api.http.GET('/api/products');
const { data: application } = await api.http.GET('/api/applications/{application_id}', {
  params: { path: { application_id: applicationId } },
  signal: abortController.signal,
});
if (!application) throw new Error('Карточка заявки не получена');

try {
  const user = await api.auth.login({ login, password });
  // user.role: 'client' | 'underwriter'. Пароль и JWT в ответе отсутствуют.
} catch (error) {
  if (error instanceof ApiError) {
    // Ориентироваться на error.code/status; fieldErrors содержат ошибки формы.
    // requestId можно показать для обращения в поддержку.
  }
}

// Idempotency-Key создаётся один раз на явную попытку отправки и сохраняется
// при ручном повторе той же попытки; expected_revision берётся из свежей карточки.
await api.http.POST('/api/applications/{application_id}/submit', {
  params: { path: { application_id: applicationId }, header: { 'Idempotency-Key': attemptKey } },
  body: { expected_revision: application.revision, consent_to_checks: true },
});
```

`http` типизирует **все** пути контракта и поддерживает GET/POST/PATCH,
параметры, JSON, заголовки, AbortSignal и `parseAs: 'blob'` для скачивания.
`uploadDocument` из `src/api/documents.ts` преобразует `File`, `document_type`
и `expected_revision` в FormData; boundary устанавливается браузером.

### Сессия и ошибки

- JWT читает и отправляет браузер через HttpOnly cookie. Клиент не обращается к
  `document.cookie`, localStorage/sessionStorage и не сохраняет пароли.
- CSRF хранится в памяти экземпляра клиента. Перед мутацией автоматически
  выполняется bootstrap; параллельные мутации делят один запрос токена.
- После успешного login/logout токен очищается и запрашивается заново. После
  `401/403` он очищается; следующий явный запрос может восстановить контекст.
- `ApiError` содержит HTTP status, серверный code, fieldErrors и requestId.
  `ApiNetworkError` означает сетевой сбой; `AbortError` сохраняет смысл отмены.
  Ошибки HTML от прокси не показываются как текст сообщения.
- Автоматических повторов мутаций и перенаправлений нет. `401`, конфликт
  ревизии `409` и бизнес-заглушка `501` обрабатываются будущими экранами #13/#14.
  TanStack Query уже настроен не повторять мутации; `queryFn` может использовать
  тот же клиент и передавать signal.
- Типы проверяют контракт на этапе сборки, а не валидируют каждый успешный JSON
  в runtime. Валидация ответа CSRF и формы серверной ошибки реализована отдельно.

Новые зависимости закреплены в `package.json`/lock-файле и перечислены в
`THIRD_PARTY_NOTICES.md`. Установочная телеметрия транзитивного `@scarf/scarf`
не запускается (точечное `allowBuilds: false` в `pnpm-workspace.yaml`).
