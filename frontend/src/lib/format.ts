const rub = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });

/** 5000000 → "5 000 000" — для шаблонов, где знак ₽ уже есть в тексте. */
export function formatNumber(value: number): string {
  return rub.format(value);
}

/** "5000000.00" → "5 000 000 ₽" (неразрывные пробелы). */
export function formatRub(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—';
  const number = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(number)) return '—';
  return `${rub.format(number)} ₽`;
}

/** Ввод пользователя → только цифры, сгруппированные для показа в поле. */
export function formatAmountInput(text: string): string {
  const digits = text.replace(/\D/g, '').replace(/^0+(?=\d)/, '');
  return digits ? rub.format(Number(digits)) : '';
}

/** Текст поля суммы → денежная строка API ("5000000.00") или null, если пусто. */
export function toMoney(text: string): string | null {
  const digits = text.replace(/\D/g, '').replace(/^0+(?=\d)/, '');
  return digits ? `${digits}.00` : null;
}

/** Денежная строка API → значение для поля ввода. */
export function moneyToInput(value: string | null | undefined): string {
  if (!value) return '';
  const whole = value.split('.')[0] ?? '';
  return formatAmountInput(whole);
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

/** Аннуитетный платёж; ставка в процентах годовых. */
export function monthlyPayment(amount: number, termMonths: number, annualRatePercent: number): number {
  if (!(amount > 0) || !(termMonths > 0)) return 0;
  const r = annualRatePercent / 100 / 12;
  if (r === 0) return amount / termMonths;
  return (amount * r) / (1 - (1 + r) ** -termMonths);
}
