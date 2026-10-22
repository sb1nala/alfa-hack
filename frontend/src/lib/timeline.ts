import type { ApplicationStatus } from './texts.ts';

export type StepState = 'done' | 'current' | 'attention' | 'stopped' | 'todo';

export type TimelineStep = { label: string; state: StepState };

/** 6 шагов для клиента (docs/product/screen-map.md, экран 5): 13 статусов сворачиваются в этапы. */
export const STEP_LABELS = [
  'Отправлена',
  'Проверка данных',
  'Оценка',
  'Решение',
  'Подписание',
  'Выдано',
] as const;

const STEP_OF: Record<ApplicationStatus, number> = {
  DRAFT: -1,
  SUBMITTED: 0,
  CHECKING: 1,
  NEEDS_INFO: 1,
  SCORING: 2,
  MANUAL_REVIEW: 2,
  APPROVED: 3,
  COUNTER_OFFER: 3,
  REJECTED: 3,
  OFFER_ACCEPTED: 4,
  SIGNED: 4,
  ISSUED: 5,
  CANCELLED: 0,
};

/** Статусы, в которых заявка меняется без действий клиента — их опрашиваем. */
export const PROCESSING: ReadonlySet<ApplicationStatus> = new Set([
  'SUBMITTED',
  'CHECKING',
  'SCORING',
  'SIGNED',
]);

export const FINAL: ReadonlySet<ApplicationStatus> = new Set(['REJECTED', 'ISSUED', 'CANCELLED']);

/**
 * currentLabel — подпись статуса для текущего шага (statuses.<S>.timeline),
 * например «Проверка специалистом» вместо «Оценка».
 */
export function buildTimeline(status: ApplicationStatus, currentLabel: string): TimelineStep[] {
  const current = STEP_OF[status];
  return STEP_LABELS.map((label, index) => {
    if (status === 'ISSUED') return { label, state: 'done' };
    if (index < current) return { label, state: 'done' };
    if (index > current) return { label, state: 'todo' };
    if (status === 'REJECTED' || status === 'CANCELLED') return { label: currentLabel, state: 'stopped' };
    if (status === 'NEEDS_INFO') return { label: currentLabel, state: 'attention' };
    return { label: currentLabel, state: 'current' };
  });
}
