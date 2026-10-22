import type { components } from '../api/schema.d.ts';
import { toMoney } from './format.ts';
import { t } from './texts.ts';

type ClientApplication = components['schemas']['ClientApplication'];
type Offer = components['schemas']['Offer'];
export type AdditionalInfoRequest = components['schemas']['AdditionalInfoRequest'];

/** В анкете нельзя запросить выручку файлом: этот пункт — поле ввода, остальные — файлы. */
export const REVENUE_ITEM = 'annual_revenue';

export const MAX_FILE_BYTES = 10 * 1024 * 1024;
export const ALLOWED_TYPES = ['application/pdf', 'image/jpeg', 'image/png'] as const;

/** Проверка файла до загрузки: тексты ошибок — errors.fileType / errors.fileTooLarge. */
export function fileError(file: { type: string; size: number }): string | null {
  if (!(ALLOWED_TYPES as readonly string[]).includes(file.type)) return t.errors.fileType;
  if (file.size > MAX_FILE_BYTES) return t.errors.fileTooLarge;
  return null;
}

/**
 * Тело POST additional-info. В контракте comment обязателен, а в интерфейсе он
 * необязательный — пустой комментарий заменяем нейтральным текстом.
 */
export function buildAdditionalInfo(
  input: { comment: string; documentIds: string[]; annualRevenue: string },
  expectedRevision: number,
): AdditionalInfoRequest {
  const revenue = toMoney(input.annualRevenue);
  return {
    expected_revision: expectedRevision,
    comment: input.comment.trim() || 'Клиент дополнил данные по запросу банка.',
    document_ids: input.documentIds,
    ...(revenue ? { annual_revenue: revenue } : {}),
  };
}

/** Все ли пункты запроса закрыты: файлы загружены, выручка введена. */
export function missingItems(
  required: string[],
  uploaded: Record<string, unknown>,
  annualRevenue: string,
): string[] {
  return required.filter((code) =>
    code === REVENUE_ITEM ? !(Number(toMoney(annualRevenue) ?? 0) > 0) : !uploaded[code]);
}

/** Предложение из последнего решения (APPROVED / COUNTER_OFFER), иначе null. */
export function currentOffer(application: Pick<ClientApplication, 'decision'>): Offer | null {
  const decision = application.decision;
  return decision && 'offer' in decision ? decision.offer : null;
}
