import type { ApiClient } from './client.ts';

// Компиляционные проверки: неверный путь, параметры и денежные числа не должны проходить.
// Функция не вызывается и не отправляет запросов. @ts-expect-error сломает typecheck,
// если генерация или API-клиент потеряют соответствующее ограничение.
export function verifyContractTypes(api: ApiClient) {
  void api.http.GET('/api/products');
  void api.http.GET('/api/applications/{application_id}', {
    params: { path: { application_id: 'example-id' } },
  });
  void api.http.POST('/api/applications', { body: { amount: '100000.00' } });
  // @ts-expect-error Путь отсутствует в контракте.
  void api.http.GET('/api/unknown');
  // @ts-expect-error GET /products не имеет метода POST.
  void api.http.POST('/api/products');
  // @ts-expect-error Не передан обязательный идентификатор пути.
  void api.http.GET('/api/applications/{application_id}');
  // @ts-expect-error Суммы в контракте — строки.
  void api.http.POST('/api/applications', { body: { amount: 100000 } });
  // @ts-expect-error Недопустимый статус фильтра.
  void api.http.GET('/api/applications', { params: { query: { status: 'UNKNOWN' } } });
  // @ts-expect-error Пароль обязателен.
  void api.auth.login({ login: 'demo-approve' });
}
