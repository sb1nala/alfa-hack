import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  buildDecisionRequest, counterReasonCodes, defaultExplanation, documentCodes, rejectionCodes,
  riskClassFrom, scoreFromFactors, validateDraft, type DecisionDraft,
} from './bankDecision.ts';

const draft = (patch: Partial<DecisionDraft>): DecisionDraft => ({
  outcome: 'APPROVED',
  productId: 'p-1',
  amount: '5 000 000',
  term: '24',
  rate: '24,5',
  counterReasons: [],
  rejectReason: '',
  documents: [],
  explanation: 'Текст клиенту',
  analytical: 'Основание',
  ...patch,
});

test('codes come from the product dictionary without _note', () => {
  assert.ok(counterReasonCodes.includes('amount_reduced'));
  assert.ok(rejectionCodes.includes('high_risk'));
  assert.ok(documentCodes.includes('bank_statement_12m'));
  for (const list of [counterReasonCodes, rejectionCodes, documentCodes]) {
    assert.equal(list.some((c) => c.startsWith('_')), false);
  }
});

test('approval request carries offer terms with a dot-decimal rate', () => {
  const body = buildDecisionRequest(draft({}), 3);
  assert.deepEqual(body, {
    expected_revision: 3,
    explanation: 'Текст клиенту',
    analytical_explanation: 'Основание',
    outcome: 'APPROVED',
    reasons: [],
    offer_terms: { product_id: 'p-1', amount: '5000000.00', term_months: 24, annual_rate_percent: '24.5' },
  });
});

test('empty rate is sent as null (rate agreed later)', () => {
  const body = buildDecisionRequest(draft({ rate: '' }), 1);
  assert.ok('offer_terms' in body && body.offer_terms.annual_rate_percent === null);
});

test('counter offer, rejection and data request map codes to dictionary texts', () => {
  const counter = buildDecisionRequest(draft({ outcome: 'COUNTER_OFFER', counterReasons: ['term_extended'] }), 1);
  assert.equal(counter.reasons[0]?.code, 'term_extended');
  assert.ok(counter.reasons[0]?.message.length);

  const rejected = buildDecisionRequest(draft({ outcome: 'REJECTED', rejectReason: 'high_risk' }), 1);
  assert.equal(rejected.outcome, 'REJECTED');
  assert.equal('offer_terms' in rejected, false);
  assert.equal(rejected.reasons[0]?.code, 'high_risk');

  const info = buildDecisionRequest(draft({ outcome: 'NEEDS_INFO', documents: ['financial_statements'] }), 1);
  assert.ok('required_documents' in info);
  assert.equal(info.required_documents[0]?.code, 'financial_statements');
  assert.ok(info.required_documents[0]?.title.length);
});

test('validation requires what the contract requires', () => {
  assert.deepEqual(validateDraft(draft({})), {});
  assert.ok(validateDraft(draft({ analytical: '  ' })).analytical);
  assert.ok(validateDraft(draft({ outcome: 'COUNTER_OFFER' })).reasons);
  assert.ok(validateDraft(draft({ outcome: 'REJECTED' })).reasons);
  assert.ok(validateDraft(draft({ outcome: 'NEEDS_INFO' })).documents);
  assert.ok(validateDraft(draft({ amount: '' })).amount);
  assert.ok(validateDraft(draft({ rate: 'abc' })).rate);
  assert.deepEqual(validateDraft(draft({ outcome: 'REJECTED', rejectReason: 'tax_debt', amount: '' })), {});
});

test('default client explanation follows the outcome', () => {
  assert.match(defaultExplanation({ outcome: 'REJECTED', counterReasons: [], rejectReason: 'tax_debt' }), /налог/i);
  assert.ok(defaultExplanation({ outcome: 'APPROVED', counterReasons: [], rejectReason: '' }).length > 10);
});

test('score sums contributions; class is read from the analytical text', () => {
  const factors = [
    { code: 'a', value: '1', contribution: '+35', explanation: '' },
    { code: 'b', value: '1', contribution: '-15', explanation: '' },
    { code: 'c', value: '1', contribution: null, explanation: '' },
  ];
  assert.equal(scoreFromFactors(factors), 20);
  assert.equal(scoreFromFactors([]), null);
  assert.equal(riskClassFrom('Класс D (65 баллов), DSCR запроса 1.06'), 'D');
  assert.equal(riskClassFrom('Стоп-факторы: tax_debt'), null);
});
