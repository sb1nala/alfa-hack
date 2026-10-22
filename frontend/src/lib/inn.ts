const W10 = [2, 4, 10, 3, 5, 9, 4, 6, 8];
const W11 = [7, 2, 4, 10, 3, 5, 9, 4, 6, 8];
const W12 = [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8];

function checkDigit(digits: number[], weights: number[]): number {
  return (weights.reduce((sum, weight, i) => sum + weight * (digits[i] ?? 0), 0) % 11) % 10;
}

/** ИНН ООО — 10 цифр, ИП — 12; проверяет и длину, и контрольные цифры ФНС. */
export function isValidInn(inn: string): boolean {
  if (!/^(\d{10}|\d{12})$/.test(inn)) return false;
  const d = [...inn].map(Number);
  if (d.length === 10) return d[9] === checkDigit(d, W10);
  return d[10] === checkDigit(d, W11) && d[11] === checkDigit(d, W12);
}

export function legalFormByInn(inn: string): 'OOO' | 'IP' | null {
  if (inn.length === 10) return 'OOO';
  if (inn.length === 12) return 'IP';
  return null;
}
