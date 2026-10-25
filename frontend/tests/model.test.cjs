'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const model = require('../assets/model.js');

function event(id, overrides = {}) {
  return {
    id, company_ids: ['alpha'], title: `Событие ${id}`, date: '2026-10-22',
    priority: 'medium', category: 'markets', category_label: 'Рынки', summary: 'Экспорт',
    relevance: { alpha: { score: 50, summary: 'Оборотный капитал', role_scores: { financial_director: 80, ceo: 30 } } },
    ...overrides,
  };
}

function fixture(events = [event('first')]) {
  return { schema_version: '3.0', is_demo: true, as_of: '2026-10-22',
    companies: [{ id: 'alpha', display_name: 'Альфа' }, { id: 'beta', display_name: 'Бета' }], events };
}

function data(events) { return model.validateDataset(fixture(events)); }

test('company and role select relevant events and change their ranking', () => {
  const dataset = data([
    event('finance'),
    event('strategy', { relevance: { alpha: { score: 60, role_scores: { financial_director: 40, ceo: 95 } } } }),
    event('other-company', { company_ids: ['beta'], relevance: { beta: { score: 99 } } }),
  ]);
  assert.deepEqual(model.getEvents(dataset, { companyId: 'alpha' }).map(e => e.id), ['finance', 'strategy']);
  assert.deepEqual(model.getEvents(dataset, { companyId: 'alpha', role: 'ceo' }).map(e => e.id), ['strategy', 'finance']);
  assert.deepEqual(model.getEvents(dataset, { companyId: 'beta' }).map(e => e.id), ['other-company']);
  assert.deepEqual(model.getEvents(dataset, { companyId: 'unknown' }), []);
});

test('search matches Russian text, relevance and categories; unknown queries return an empty result', () => {
  const dataset = data([event('one')]);
  for (const query of ['  ЭКСПОРТ  ', 'оборотный', 'рынки', 'событие']) {
    assert.equal(model.getEvents(dataset, { companyId: 'alpha', query }).length, 1, query);
  }
  assert.deepEqual(model.getEvents(dataset, { companyId: 'alpha', query: 'несуществующий запрос' }), []);
});

test('category, priority and date filters combine without mutating the fixture', () => {
  const dataset = data([
    event('old', { date: '2026-09-20', priority: 'high' }),
    event('recent', { date: '2026-10-20', priority: 'high' }),
    event('regulation', { date: '2026-10-21', category: 'regulation', priority: 'high' }),
    event('lower', { priority: 'low' }),
  ]);
  const original = JSON.stringify(dataset);
  assert.deepEqual(model.getEvents(dataset, { companyId: 'alpha', category: 'markets', priority: 'high', period: '7' }).map(e => e.id), ['recent']);
  assert.deepEqual(model.getEvents(dataset, { companyId: 'alpha', sort: 'date' }).map(e => e.id), ['lower', 'regulation', 'recent', 'old']);
  assert.equal(JSON.stringify(dataset), original);
});

test('saved view honours an empty list and only returns saved events for the selected company', () => {
  const dataset = data([event('one'), event('two', { company_ids: ['beta'] })]);
  assert.deepEqual(model.getEvents(dataset, { companyId: 'alpha', bookmarked: [] }), []);
  assert.deepEqual(model.getEvents(dataset, { companyId: 'alpha', bookmarked: ['one', 'two'] }).map(e => e.id), ['one']);
});

test('optional analysis data normalizes to safe incomplete states', () => {
  const dataset = data();
  const insight = dataset.events[0];
  assert.equal(insight.bank_view.status, 'not_loaded');
  assert.equal(insight.bank_view.summary, null);
  assert.deepEqual(insight.sources, []);
  assert.deepEqual(insight.facts, []);
  assert.deepEqual(insight.actions, []);
  assert.deepEqual(insight.ai_interpretation.risks, []);
  assert.deepEqual(insight.ai_interpretation.opportunities, []);
  assert.deepEqual(insight.suggested_questions, []);
  assert.equal(insight.verification.status, 'needs_more_data');
  assert.equal(model.impact(insight, 'beta', 'ceo'), 'Интерпретация ещё не подготовлена.');
  const sparse = data([event('sparse', { ai_interpretation: {
    risks: [null, 'invalid', { text: 'Условный риск' }],
    opportunities: [null, {}], assumptions: [null, 'Предположение'], reasoning_steps: [null, 'Проверка'],
  } })]).events[0].ai_interpretation;
  assert.deepEqual(sparse.risks, [{ text: 'Условный риск' }]);
  assert.deepEqual(sparse.opportunities, []);
  assert.deepEqual(sparse.assumptions, ['Предположение']);
  assert.deepEqual(sparse.reasoning_steps, ['Проверка']);
});

test('role-specific impact falls back to company interpretation then generic analysis', () => {
  const insight = data([event('one', { relevance: { alpha: { role_summary: { ceo: 'Стратегия' }, summary: 'Для компании' } }, ai_interpretation: { summary: 'Общее демо' } })]).events[0];
  assert.equal(model.impact(insight, 'alpha', 'ceo'), 'Стратегия');
  assert.equal(model.impact(insight, 'alpha', 'treasurer'), 'Для компании');
  assert.equal(model.impact(insight, 'beta', 'ceo'), 'Общее демо');
});

test('invalid envelopes, duplicate IDs and unknown company references reject the payload', () => {
  for (const malformed of [null, {}, { ...fixture(), schema_version: '2.0' }, { ...fixture(), is_demo: undefined }]) {
    assert.throws(() => model.validateDataset(malformed));
  }
  const duplicateCompany = fixture();
  duplicateCompany.companies.push({ ...duplicateCompany.companies[0] });
  assert.throws(() => model.validateDataset(duplicateCompany), /идентификатор/);
  assert.throws(() => data([event('one'), event('one')]), /идентификатор/);
  assert.throws(() => data([event('one', { company_ids: ['missing'] })]), /обязательные/);
  assert.throws(() => data([event('one', { title: '' })]), /обязательные/);
  assert.throws(() => data([event('one', { sources: [{ id: 'same' }, { id: 'same' }] })]), /идентификатор/);
});

test('invalid dates and inherited object keys cannot pass as valid event fields', () => {
  assert.throws(() => data([event('one', { date: '2026-99-99' })]), /обязательные/);
  assert.throws(() => data([event('one', { date: '2026-10-22 garbage' })]), /обязательные/);
  for (const priority of ['urgent', 'toString', '__proto__']) {
    assert.throws(() => data([event('one', { priority })]), /обязательные/);
  }
  for (const as_of of ['not-a-date', '2026-99-99', '2026-02-31']) {
    assert.throws(() => model.validateDataset({ ...fixture(), as_of }), /дата/);
  }
});

test('facts retain a visible source-availability flag instead of implying unsupported confirmation', () => {
  const insight = data([event('one', { sources: [{ id: 'source-one' }], facts: [
    { id: 'f1', text: 'Есть источник', source_id: 'source-one' },
    { id: 'f2', text: 'Нет источника', source_id: 'unknown' },
  ] })]).events[0];
  assert.equal(insight.facts[0].has_source, true);
  assert.equal(insight.facts[1].has_source, false);
});

test('bank attribution requires a real accessible bank document for every cited source', () => {
  const bank = { status: 'available', summary: 'Подтверждённая аналитика банка', source_ids: ['bank'] };
  const source = { id: 'bank', type: 'bank_analysis', is_demo: false, url: 'https://example.com/bank-analysis' };
  for (const overrides of [
    { sources: [] },
    { sources: [{ ...source, is_demo: true }] },
    { sources: [{ ...source, type: 'news' }] },
    { sources: [{ ...source, url: 'javascript:alert(1)' }] },
    { sources: [source], bank_view: { ...bank, source_ids: ['bank', 'missing'] } },
    { sources: [source], bank_view: { ...bank, summary: ' ' } },
  ]) {
    const insight = data([event('one', { bank_view: bank, ...overrides })]).events[0];
    assert.equal(insight.bank_view.status, 'not_loaded');
    assert.equal(insight.bank_view.summary, null);
  }
  const available = data([event('one', { sources: [source], bank_view: bank })]).events[0];
  assert.equal(available.bank_view.status, 'available');
  assert.equal(available.bank_view.summary, bank.summary);
});

test('HTML and link helpers contain untrusted display data', () => {
  assert.equal(model.escape('<img src=x onerror="alert(1)"> & \'quoted\''), '&lt;img src=x onerror=&quot;alert(1)&quot;&gt; &amp; &#39;quoted&#39;');
  assert.equal(model.escape(null), '');
  for (const url of ['javascript:alert(1)', 'data:text/html,<script>alert(1)</script>', 'file:///C:/secret', '/relative', 'not a URL']) assert.equal(model.safeUrl(url), null);
  assert.equal(model.safeUrl('https://example.com/report'), 'https://example.com/report');
});

test('state keys isolate company/event/date and profile values describe missing data accurately', () => {
  assert.notEqual(model.stateKey('alpha', event('one')), model.stateKey('beta', event('one')));
  assert.notEqual(model.stateKey('alpha', event('one')), model.stateKey('alpha', event('one', { date: '2026-10-21' })));
  assert.equal(model.featureText({ value: false }), 'Нет');
  assert.equal(model.featureText({ value: null }), 'Нет данных');
  assert.match(model.featureText({ value: 12000000000, unit: 'RUB' }), /12.*млрд ₽/);
});

test('delivered demo fixture has company coverage, source links and truthful demo provenance', () => {
  const fixturePath = path.join(__dirname, '../data/demo.json');
  assert.ok(fs.existsSync(fixturePath), 'The demo fixture must ship with the application');
  const dataset = model.validateDataset(JSON.parse(fs.readFileSync(fixturePath, 'utf8')));
  assert.equal(dataset.is_demo, true);
  assert.ok(dataset.companies.length >= 2, 'The company switcher must demonstrate personalization');
  assert.ok(dataset.events.length >= 3, 'The feed must contain enough events to demonstrate filters');
  for (const company of dataset.companies) {
    const finance = model.getEvents(dataset, { companyId: company.id, role: 'financial_director' });
    const strategy = model.getEvents(dataset, { companyId: company.id, role: 'ceo' });
    assert.ok(finance.length, company.id);
    assert.notDeepEqual(finance.map(e => e.id), strategy.map(e => e.id), `${company.id}: role switch must change visible ranking`);
    assert.ok(finance.some(e => model.impact(e, company.id, 'financial_director') !== model.impact(e, company.id, 'ceo')), `${company.id}: role switch must change the explanation`);
  }
  for (const insight of dataset.events) {
    assert.equal(insight.bank_view.status, 'not_loaded', `${insight.id}: fictional analysis must not be attributed to the bank`);
    assert.equal(insight.bank_view.summary, null);
    assert.equal(insight.ai_interpretation.is_demo, true);
    assert.equal(insight.ai_interpretation.status, 'prewritten_demo');
    assert.ok(insight.facts.every(f => f.has_source), `${insight.id}: each fixture fact needs inspectable source text`);
    assert.ok(insight.sources.every(s => s.is_demo === true && !s.url), `${insight.id}: fictional sources cannot impersonate real publications`);
    for (const collection of [insight.facts, insight.actions, insight.ai_interpretation.risks, insight.ai_interpretation.opportunities]) {
      assert.equal(new Set(collection.map(item => item.id)).size, collection.length, `${insight.id}: stable identifiers must be unique within their collection`);
    }
    for (const interpretation of [insight.ai_interpretation, ...insight.ai_interpretation.risks, ...insight.ai_interpretation.opportunities, ...insight.actions]) {
      assert.ok(interpretation.basis, `${insight.id}: interpretations and actions need inspectable evidence`);
      for (const id of interpretation.basis.fact_ids) assert.ok(insight.facts.some(f => f.id === id), `${insight.id}: dangling fact reference ${id}`);
      assert.deepEqual(interpretation.basis.bank_source_ids, [], `${insight.id}: missing bank material cannot be used as evidence`);
      for (const companyId of insight.company_ids) {
        const company = dataset.companies.find(c => c.id === companyId);
        for (const field of interpretation.basis.company_feature_refs) assert.ok(company.features.some(f => f.field === field), `${insight.id}: ${companyId} missing referenced feature ${field}`);
      }
    }
  }
});
