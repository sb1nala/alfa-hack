import type { components } from '../api/schema.d.ts';
import { toMoney } from './format.ts';
import { statusTexts, t } from './texts.ts';

export type ManualOutcome = 'APPROVED' | 'COUNTER_OFFER' | 'NEEDS_INFO' | 'REJECTED';
export type ManualDecisionRequest =
  | components['schemas']['ManualApprovalRequest']
  | components['schemas']['ManualCounterOfferRequest']
  | components['schemas']['ManualNeedsInfoRequest']
  | components['schemas']['ManualRejectionRequest'];
type ScoringFactor = components['schemas']['ScoringFactor'];

export type DecisionDraft = {
  outcome: ManualOutcome;
  productId: string;
  amount: string;
  term: string;
  rate: string;
  counterReasons: string[];
  rejectReason: string;
  documents: string[];
  explanation: string;
  analytical: string;
};

const counterTexts = t.counterOfferReasons as unknown as Record<string, string>;
const rejectionTexts = t.rejectionReasons as unknown as Record<string, { message: string; advice: string }>;
const documentTexts = t.requiredDocuments as unknown as Record<string, { title: string; explanation: string }>;

const visible = (record: Record<string, unknown>) => Object.keys(record).filter((k) => !k.startsWith('_'));
export const counterReasonCodes = visible(counterTexts);
export const rejectionCodes = visible(rejectionTexts);
export const documentCodes = visible(documentTexts);

export function counterReasonText(code: string): string {
  return counterTexts[code] ?? code;
}
export function rejectionText(code: string): { message: string; advice: string } | undefined {
  return rejectionTexts[code];
}
export function documentText(code: string): { title: string; explanation: string } | undefined {
  return documentTexts[code];
}

/** Текст для клиента по умолчанию — из словаря продукта; аналитик может его поправить. */
export function defaultExplanation(draft: Pick<DecisionDraft, 'outcome' | 'counterReasons' | 'rejectReason'>): string {
  switch (draft.outcome) {
    case 'APPROVED':
      return statusTexts('APPROVED').description;
    case 'NEEDS_INFO':
      return statusTexts('NEEDS_INFO').description;
    case 'COUNTER_OFFER':
      return draft.counterReasons.map(counterReasonText).join(' ') || statusTexts('COUNTER_OFFER').description;
    case 'REJECTED': {
      const text = rejectionText(draft.rejectReason);
      return text ? `${text.message} ${text.advice}` : statusTexts('REJECTED').description;
    }
  }
}

export type DraftErrors = Partial<Record<'product' | 'amount' | 'term' | 'rate' | 'reasons' | 'documents' | 'explanation' | 'analytical', string>>;

export function validateDraft(draft: DecisionDraft): DraftErrors {
  const errors: DraftErrors = {};
  const required = 'Обязательное поле';
  if (!draft.analytical.trim()) errors.analytical = required;
  if (!draft.explanation.trim()) errors.explanation = required;
  if (draft.outcome === 'APPROVED' || draft.outcome === 'COUNTER_OFFER') {
    if (!draft.productId) errors.product = required;
    if (!(Number(toMoney(draft.amount) ?? 0) > 0)) errors.amount = required;
    if (!(Number(draft.term) > 0)) errors.term = required;
    if (draft.rate && !/^\d+([.,]\d+)?$/.test(draft.rate.trim())) errors.rate = 'Число, например 24,5';
  }
  if (draft.outcome === 'COUNTER_OFFER' && draft.counterReasons.length === 0) errors.reasons = 'Выберите хотя бы одну причину';
  if (draft.outcome === 'REJECTED' && !draft.rejectReason) errors.reasons = 'Выберите причину отказа';
  if (draft.outcome === 'NEEDS_INFO' && draft.documents.length === 0) errors.documents = 'Выберите хотя бы один пункт';
  return errors;
}

/** Черновик формы → тело POST /api/underwriter/applications/{id}/decision по контракту. */
export function buildDecisionRequest(draft: DecisionDraft, expectedRevision: number): ManualDecisionRequest {
  const base = {
    expected_revision: expectedRevision,
    explanation: draft.explanation.trim(),
    analytical_explanation: draft.analytical.trim(),
  };
  const offerTerms = () => ({
    product_id: draft.productId,
    amount: toMoney(draft.amount) ?? '0.00',
    term_months: Number(draft.term),
    annual_rate_percent: draft.rate.trim() ? draft.rate.trim().replace(',', '.') : null,
  });
  switch (draft.outcome) {
    case 'APPROVED':
      return { ...base, outcome: 'APPROVED', reasons: [], offer_terms: offerTerms() };
    case 'COUNTER_OFFER':
      return {
        ...base,
        outcome: 'COUNTER_OFFER',
        reasons: draft.counterReasons.map((code) => ({ code, message: counterReasonText(code) })),
        offer_terms: offerTerms(),
      };
    case 'NEEDS_INFO':
      return {
        ...base,
        outcome: 'NEEDS_INFO',
        reasons: [],
        required_documents: draft.documents.map((code) => ({
          code,
          title: documentText(code)?.title ?? code,
          explanation: documentText(code)?.explanation ?? '',
        })),
      };
    case 'REJECTED':
      return {
        ...base,
        outcome: 'REJECTED',
        reasons: [{ code: draft.rejectReason, message: rejectionText(draft.rejectReason)?.message ?? draft.rejectReason }],
      };
  }
}

/** Балл = сумма вкладов факторов (decide() отдаёт вклад строкой вида "+35"). */
export function scoreFromFactors(factors: ScoringFactor[]): number | null {
  const values = factors.map((f) => Number(f.contribution)).filter(Number.isFinite);
  return values.length ? values.reduce((a, b) => a + b, 0) : null;
}

/**
 * Класс риска пока не отдельное поле контракта, он есть в аналитическом объяснении
 * («Класс B (130 баллов) …»). Когда BankDecision получит risk_class — читать его.
 */
export function riskClassFrom(analytical: string): string | null {
  return /Класс\s+([A-E])\b/.exec(analytical)?.[1] ?? null;
}
