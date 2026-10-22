import texts from '../../../docs/product/ui-texts.ru.json' with { type: 'json' };
import type { components } from '../api/schema.d.ts';

/** Тексты интерфейса — единственный источник: docs/product/ui-texts.ru.json (владелец: Продукт). */
export const t = texts;

export type ApplicationStatus = components['schemas']['ApplicationStatus'];

type StatusTexts = {
  timeline: string;
  title: string;
  description: string;
  next: string | null;
  bank: string;
  slow?: string;
};

export function statusTexts(status: ApplicationStatus): StatusTexts {
  return texts.statuses[status] as StatusTexts;
}

/** Подставляет значения в плейсхолдеры вида {name}; неизвестные оставляет как есть. */
export function fill(template: string, values: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (match, key: string) =>
    key in values ? String(values[key]) : match);
}

type ProductTexts = { name: string; short: string; description: string; params: string };

export function productTexts(code: string): ProductTexts | undefined {
  const entry = (texts.products as Record<string, unknown>)[code];
  return code.startsWith('_') ? undefined : (entry as ProductTexts | undefined);
}

export type PurposeCode = Exclude<keyof typeof texts.purposes, '_note'>;

export const purposeCodes = Object.keys(texts.purposes).filter(
  (key) => !key.startsWith('_'),
) as PurposeCode[];

export function purposeTexts(code: string): { label: string; products: string[] } | undefined {
  if (code.startsWith('_')) return undefined;
  return (texts.purposes as unknown as Record<string, { label: string; products: string[] }>)[code];
}
