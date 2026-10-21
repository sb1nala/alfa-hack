import type { components } from './schema.d.ts';
import texts from '../../../docs/product/ui-texts.ru.json' with { type: 'json' };

export type FieldError = components['schemas']['FieldError'];
type ErrorResponse = components['schemas']['ErrorResponse'];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isFieldError(value: unknown): value is FieldError {
  return isRecord(value) && typeof value.field === 'string' &&
    typeof value.code === 'string' && typeof value.message === 'string';
}

function isErrorResponse(value: unknown): value is ErrorResponse {
  return isRecord(value) && typeof value.code === 'string' &&
    typeof value.message === 'string' && typeof value.request_id === 'string' &&
    Array.isArray(value.field_errors) && value.field_errors.every(isFieldError);
}

/** Серверная ошибка: ориентироваться на code, поля формы — в fieldErrors. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly fieldErrors: FieldError[];
  readonly requestId: string | null;

  constructor(status: number, payload: unknown, requestId: string | null = null) {
    const valid = isErrorResponse(payload) ? payload : undefined;
    super(valid?.message ?? texts.errors.generic);
    this.name = 'ApiError';
    this.status = status;
    this.code = valid?.code ?? 'http_error';
    this.fieldErrors = valid?.field_errors ?? [];
    this.requestId = valid?.request_id ?? requestId;
  }
}

export class ApiNetworkError extends Error {
  constructor() {
    super(texts.errors.network);
    this.name = 'ApiNetworkError';
  }
}

export class ApiProtocolError extends Error {
  constructor() {
    super('Сервер вернул ответ в неожиданном формате.');
    this.name = 'ApiProtocolError';
  }
}
