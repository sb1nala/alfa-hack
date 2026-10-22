import { Alert } from '@mui/material';
import { ApiError, ApiNetworkError } from '../api/index.ts';
import { t } from '../lib/texts.ts';

/** Понятный текст ошибки по коду ответа; детали сервера показываем, только если они безопасны. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiNetworkError) return t.errors.network;
  if (error instanceof ApiError) {
    if (error.status === 401) return t.errors.sessionExpired;
    if (error.status === 403) return t.errors.forbidden;
    if (error.status === 404) return t.errors.notFound;
    if (error.status === 409) return t.errors.conflict;
    if (error.status === 422 || error.status === 400) return t.errors.validation;
  }
  return t.errors.generic;
}

export function ErrorAlert({ error }: { error: unknown }) {
  return <Alert severity="error">{errorMessage(error)}</Alert>;
}
