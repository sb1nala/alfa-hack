/** Подписи для панели «Конвейер сейчас» в кабинете аналитика. */

/** 1 заявка, 2 заявки, 5 заявок; forms — «одна|две|пять». */
export function plural(count: number, forms: string): string {
  const [one, few, many] = forms.split('|');
  const n = Math.abs(count) % 100;
  if (n % 10 === 1 && n !== 11) return one ?? '';
  if (n % 10 >= 2 && n % 10 <= 4 && (n < 12 || n > 14)) return few ?? '';
  return many ?? '';
}

/** 2.1 → «2,1 с», 840 → «14 мин», 4000 → «1 ч 6 мин», 200000 → «2 дн 7 ч». */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '—';
  if (seconds < 60) {
    const value = seconds < 10 ? Math.round(seconds * 10) / 10 : Math.round(seconds);
    return `${String(value).replace('.', ',')} с`;
  }
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} мин`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return minutes % 60 ? `${hours} ч ${minutes % 60} мин` : `${hours} ч`;
  const days = Math.floor(hours / 24);
  return hours % 24 ? `${days} дн ${hours % 24} ч` : `${days} дн`;
}

/** Доля в процентах без дробной части; null, если знаменатель нулевой. */
export function share(part: number, total: number): number | null {
  return total > 0 ? Math.round((part / total) * 100) : null;
}
