import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildAdditionalInfo, currentOffer, fileError, MAX_FILE_BYTES, missingItems } from './clientActions.ts';

test('file validation follows the UI limits', () => {
  assert.equal(fileError({ type: 'application/pdf', size: 1000 }), null);
  assert.equal(fileError({ type: 'image/png', size: MAX_FILE_BYTES }), null);
  assert.match(fileError({ type: 'application/zip', size: 10 }) ?? '', /PDF/);
  assert.match(fileError({ type: 'image/jpeg', size: MAX_FILE_BYTES + 1 }) ?? '', /10 МБ/);
});

test('additional info always has a comment and sends revenue only when given', () => {
  assert.deepEqual(buildAdditionalInfo({ comment: '  ', documentIds: ['d1'], annualRevenue: '' }, 4), {
    expected_revision: 4,
    comment: 'Клиент дополнил данные по запросу банка.',
    document_ids: ['d1'],
  });
  const withRevenue = buildAdditionalInfo({ comment: 'Выписка', documentIds: [], annualRevenue: '9 400 000' }, 5);
  assert.equal(withRevenue.comment, 'Выписка');
  assert.equal(withRevenue.annual_revenue, '9400000.00');
});

test('missing items: files by code, revenue by field', () => {
  const required = ['annual_revenue', 'bank_statement_12m'];
  assert.deepEqual(missingItems(required, {}, ''), required);
  assert.deepEqual(missingItems(required, { bank_statement_12m: { id: 'x' } }, ''), ['annual_revenue']);
  assert.deepEqual(missingItems(required, { bank_statement_12m: { id: 'x' } }, '9 400 000'), []);
});

test('current offer comes only from decisions with an offer', () => {
  const offer = { id: 'o1', created_at: '2026-10-22T00:00:00Z', terms: { product_id: 'p', amount: '1.00', term_months: 1 } };
  assert.equal(currentOffer({ decision: null }), null);
  assert.equal(currentOffer({
    decision: { id: 'd', created_at: '', outcome: 'REJECTED', reasons: [{ code: 'x', message: 'y' }], explanation: 'e', required_documents: [] },
  }), null);
  assert.equal(currentOffer({
    decision: { id: 'd', created_at: '', outcome: 'APPROVED', reasons: [], explanation: 'e', required_documents: [], offer },
  })?.id, 'o1');
});
