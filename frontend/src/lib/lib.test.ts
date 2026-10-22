import { test } from 'node:test';
import assert from 'node:assert/strict';
import { formatAmountInput, formatRub, moneyToInput, monthlyPayment, toMoney } from './format.ts';
import { isValidInn, legalFormByInn } from './inn.ts';
import { productRule } from './products.ts';
import { fill, productTexts, purposeCodes, purposeTexts, statusTexts } from './texts.ts';
import { buildTimeline, FINAL, PROCESSING } from './timeline.ts';
import seed from '../../../seed/companies.json' with { type: 'json' };

const plain = (s: string) => s.replace(/[  ]/g, ' ');

test('formatRub groups thousands and handles empty values', () => {
  assert.equal(plain(formatRub('5000000.00')), '5 000 000 ₽');
  assert.equal(plain(formatRub(1500)), '1 500 ₽');
  assert.equal(formatRub(null), '—');
  assert.equal(formatRub('abc'), '—');
});

test('amount input keeps digits only and converts to API money', () => {
  assert.equal(plain(formatAmountInput('5 000 000 руб')), '5 000 000');
  assert.equal(formatAmountInput(''), '');
  assert.equal(toMoney('5 000 000'), '5000000.00');
  assert.equal(toMoney('007'), '7.00');
  assert.equal(toMoney(''), null);
  assert.equal(plain(moneyToInput('47000000.00')), '47 000 000');
});

test('monthlyPayment is an annuity', () => {
  assert.equal(Math.round(monthlyPayment(1_000_000, 12, 12) * 100) / 100, 88848.79);
  assert.equal(monthlyPayment(0, 12, 12), 0);
  assert.equal(monthlyPayment(1200, 12, 0), 100);
});

test('every synthetic INN passes the checksum, a corrupted one does not', () => {
  for (const company of seed.companies) {
    assert.ok(isValidInn(company.company.inn), company.company.inn);
    assert.equal(legalFormByInn(company.company.inn), company.company.legal_form);
  }
  assert.equal(isValidInn('0086906650'), false);
  assert.equal(isValidInn('12345'), false);
});

test('product limits come from seed/products.json', () => {
  const express = productRule('express');
  assert.deepEqual(
    express && [express.minAmount, express.maxAmount, express.maxTerm, express.baseRatePercent],
    [300000, 5000000, 36, 26],
  );
  assert.equal(productRule('mortgage'), undefined);
});

test('texts helpers', () => {
  assert.equal(fill('От {min} до {max} ₽', { min: 1, max: 2 }), 'От 1 до 2 ₽');
  assert.equal(fill('{unknown}', {}), '{unknown}');
  assert.ok(purposeCodes.includes('buy_equipment'));
  assert.equal(purposeCodes.includes('_note' as never), false);
  assert.deepEqual(purposeTexts('buy_equipment')?.products, ['investment', 'express']);
  assert.equal(productTexts('express')?.name, 'Экспресс-кредит');
  assert.ok(statusTexts('CHECKING').slow);
});

test('timeline folds statuses into 6 client steps', () => {
  const states = (s: Parameters<typeof buildTimeline>[0]) =>
    buildTimeline(s, statusTexts(s).timeline).map((step) => step.state).join(',');
  assert.equal(states('SUBMITTED'), 'current,todo,todo,todo,todo,todo');
  assert.equal(states('CHECKING'), 'done,current,todo,todo,todo,todo');
  assert.equal(states('NEEDS_INFO'), 'done,attention,todo,todo,todo,todo');
  assert.equal(states('MANUAL_REVIEW'), 'done,done,current,todo,todo,todo');
  assert.equal(states('REJECTED'), 'done,done,done,stopped,todo,todo');
  assert.equal(states('ISSUED'), 'done,done,done,done,done,done');
  const manual = buildTimeline('MANUAL_REVIEW', statusTexts('MANUAL_REVIEW').timeline);
  assert.equal(manual[2]?.label, 'Проверка специалистом');
  assert.ok(PROCESSING.has('CHECKING') && !PROCESSING.has('MANUAL_REVIEW'));
  assert.ok(FINAL.has('ISSUED') && !FINAL.has('APPROVED'));
});
