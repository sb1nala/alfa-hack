/**
 * Симуляция подтверждения подписи кодом (ответ организаторов на вебинаре, вопрос 12).
 * Код живёт только в браузере и показывается на экране: SMS кейс запрещает, а боевая
 * подпись — в интернет-банке для бизнеса или через Госключ (docs/architecture.md).
 */
export const CODE_LENGTH = 6;
export const MAX_ATTEMPTS = 3;

export function generateCode(random: (buffer: Uint32Array) => Uint32Array = (b) => crypto.getRandomValues(b)): string {
  const [value = 0] = random(new Uint32Array(1));
  return String(value % 10 ** CODE_LENGTH).padStart(CODE_LENGTH, '0');
}

/** «482913» → «482 913»: так код легче прочитать с экрана. */
export function formatCode(code: string): string {
  return `${code.slice(0, 3)} ${code.slice(3)}`;
}

/** Оставляем только цифры: пробелы и дефисы при вставке не мешают. */
export function normalizeCode(input: string): string {
  return input.replace(/\D/g, '').slice(0, CODE_LENGTH);
}

export type CodeCheck = 'ok' | 'incomplete' | 'mismatch' | 'locked';

export function checkCode(expected: string | null, input: string, attempts: number): CodeCheck {
  if (!expected || attempts >= MAX_ATTEMPTS) return 'locked';
  const code = normalizeCode(input);
  if (code.length < CODE_LENGTH) return 'incomplete';
  return code === expected ? 'ok' : 'mismatch';
}
