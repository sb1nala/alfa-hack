import seed from '../../../seed/products.json' with { type: 'json' };

/**
 * Лимиты продуктов — из seed/products.json (владелец: Риски). Нужны для подсказок и
 * предварительной проверки анкеты; окончательно условия проверяет сервер.
 */
export type ProductRule = {
  code: string;
  minAmount: number;
  maxAmount: number;
  minTerm: number;
  maxTerm: number;
  baseRatePercent: number;
};

const RULES: Record<string, ProductRule> = Object.fromEntries(
  seed.products.map((p) => [
    p.code,
    {
      code: p.code,
      minAmount: Number(p.min_amount),
      maxAmount: Number(p.max_amount),
      minTerm: p.min_term_months,
      maxTerm: p.max_term_months,
      baseRatePercent: Number(p.base_rate_percent),
    },
  ]),
);

export function productRule(code: string | undefined): ProductRule | undefined {
  return code ? RULES[code] : undefined;
}
