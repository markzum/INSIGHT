'use strict';

// Network-free integration of the real application renderer and delegated events.
// This tiny DOM adapter does not test browser layout, actual focus behavior or CSS.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..');
const modelSource = fs.readFileSync(path.join(root, 'assets/model.js'), 'utf8');
const appSource = fs.readFileSync(path.join(root, 'assets/app.js'), 'utf8');
const shellSource = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const fixture = JSON.parse(fs.readFileSync(path.join(root, 'data/demo.json'), 'utf8'));
const clone = value => structuredClone(value);
const pause = () => new Promise(resolve => setTimeout(resolve, 20));
const decode = value => String(value).replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');

function attrs(tag) {
  const result = {};
  for (const match of tag.matchAll(/([\w-]+)(?:="([^"]*)")?/g)) result[match[1]] = decode(match[2] ?? '');
  return result;
}

function harness(options = {}) {
  const nodes = new Map(), docListeners = new Map(), winListeners = new Map(), timers = new Set();
  const storage = options.storage || new Map();
  const errors = [], requests = [];
  let hash = options.hash || '#overview';
  class Element {
    constructor(attributes = {}) {
      this.attributes = { ...attributes }; this.id = attributes.id || ''; this.dataset = {};
      for (const [key, value] of Object.entries(attributes)) if (key.startsWith('data-')) this.dataset[key.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = value;
      this._html = ''; this.children = []; this.value = attributes.value || ''; this.checked = 'checked' in attributes;
      this.className = attributes.class || ''; this.disabled = false; this.hidden = 'hidden' in attributes; this.isConnected = true;
      this.classList = { add: name => { this.className += ` ${name}`; }, remove: name => { this.className = this.className.split(' ').filter(item => item !== name).join(' '); } };
    }
    get innerHTML() { return this._html; }
    set innerHTML(value) {
      this._html = String(value);
      for (const child of this.children) { child.isConnected = false; if (nodes.get(child.id) === child) nodes.delete(child.id); }
      this.children = [];
      for (const match of this._html.matchAll(/<[a-z][^>]*\bid="[^"]+"[^>]*>/gi)) {
        const child = new Element(attrs(match[0])); nodes.set(child.id, child); this.children.push(child);
      }
      if (this._html.includes('id="question-form"')) {
        const child = new Element({ id: 'question-submit', type: 'submit' }); nodes.set('question-submit', child); this.children.push(child);
      }
      if (this.id === 'company-select') {
        const options = [...this._html.matchAll(/<option\b[^>]*>/g)].map(m => attrs(m[0]));
        this.value = (options.find(o => 'selected' in o) || options[0])?.value || '';
      }
    }
    get textContent() { return decode(this._html.replace(/<[^>]*>/g, '')); }
    set textContent(value) { this._html = String(value); }
    get innerText() { return this.textContent; }
    setAttribute(key, value) { this.attributes[key] = String(value); }
    getAttribute(key) { return this.attributes[key] ?? null; }
    closest(selector) { return (selector === '[data-action]' && this.dataset.action) || (selector === '[data-tab]' && this.dataset.tab) || (selector === '.skip-link' && this.className.split(' ').includes('skip-link')) ? this : null; }
    focus() { document.activeElement = this; }
    scrollIntoView() { this.scrolledIntoView = true; }
    addEventListener() {}
    showModal() { this.open = true; }
    close() { this.open = false; }
  }
  for (const id of ['main', 'navigation', 'company-select', 'role-select', 'toast', 'modal', 'modal-title', 'modal-body']) nodes.set(id, new Element({ id }));
  const document = {
    activeElement: null,
    querySelector(selector) { return nodes.get(selector === '#question-form button' ? 'question-submit' : selector.replace(/^#/, '')) || null; },
    querySelectorAll(selector) {
      if (selector !== '[data-action]') return [];
      return [shellSource, ...[...nodes.values()].map(node => node.innerHTML)].flatMap(html => [...html.matchAll(/<[a-z][^>]*\bdata-action="[^"]+"[^>]*>/gi)].map(match => new Element(attrs(match[0]))));
    },
    addEventListener(name, fn) { if (!docListeners.has(name)) docListeners.set(name, []); docListeners.get(name).push(fn); },
  };
  const emitWindow = name => { for (const fn of winListeners.get(name) || []) { try { fn(); } catch (error) { errors.push(error); } } };
  const location = { search: options.search || '', protocol: options.protocol || 'https:' };
  Object.defineProperty(location, 'hash', { get: () => hash, set(value) { const changed = hash !== value; hash = value; if (changed) queueMicrotask(() => emitWindow('hashchange')); } });
  const config = { apiBaseUrl: options.api ? '/api' : '', requestTimeoutMs: 1000 };
  const sandbox = {
    document, location, console, URL, URLSearchParams, AbortController, structuredClone, Intl,
    history: { replaceState(_state, _title, url) { hash = url; } },
    localStorage: {
      getItem(key) { if (options.storageDenied) throw new Error('Storage unavailable'); return storage.get(key) ?? null; },
      setItem(key, value) { if (options.storageDenied) throw new Error('Storage unavailable'); storage.set(key, value); },
    },
    scrollY: 0, scrollTo() {}, ALFA_CONFIG: config, ALFA_DEMO: clone(options.data || fixture),
    addEventListener(name, fn) { if (!winListeners.has(name)) winListeners.set(name, []); winListeners.get(name).push(fn); },
    setTimeout(fn, ms) { const timer = setTimeout(() => { timers.delete(timer); fn(); }, [350, 400].includes(ms) ? 5 : ms); timers.add(timer); return timer; },
    clearTimeout(timer) { timers.delete(timer); clearTimeout(timer); },
    fetch: async (url, init = {}) => {
      requests.push({ url, init });
      if (options.fetch) return options.fetch(url, init);
      return { ok: true, status: 200, json: async () => clone(options.data || fixture) };
    },
  };
  sandbox.window = sandbox;
  const context = vm.createContext(sandbox);
  vm.runInContext(modelSource, context, { filename: 'assets/model.js' });
  vm.runInContext(appSource, context, { filename: 'assets/app.js' });
  function emit(name, target, extra = {}) { const event = { target, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...extra }; for (const fn of docListeners.get(name) || []) fn(event); return event; }
  const get = id => { const node = nodes.get(id); assert.ok(node, `Expected rendered #${id}`); return node; };
  return {
    document, nodes, storage, requests, errors, context, config,
    hash: () => hash,
    get,
    html: (id = 'main') => get(id).innerHTML,
    async ready() { await pause(); assert.equal(get('main').getAttribute('aria-busy'), 'false'); assert.deepEqual(errors, []); },
    async go(value) { location.hash = value; await pause(); assert.deepEqual(errors, []); },
    change(id, value) { const node = get(id); node.value = value; emit('change', node); },
    input(id, value) { const node = get(id); node.value = value; emit('input', node); },
    action(name, extra = {}) { const button = document.querySelectorAll('[data-action]').find(node => node.dataset.action === name && Object.entries(extra).every(([key, value]) => node.dataset[key] === value)); assert.ok(button, `Expected rendered action ${name} ${JSON.stringify(extra)}`); emit('click', button); return button; },
    tab(key) { emit('click', get(`tab-${key}`)); },
    key(key) { emit('keydown', document.activeElement, { key }); },
    skip() { const tag = shellSource.match(/<a\b[^>]*class="skip-link"[^>]*>/)?.[0]; assert.ok(tag, 'The shell must expose its skip link'); return emit('click', new Element(attrs(tag))); },
    check(eventId, actionId, checked) { const control = document.querySelectorAll('[data-action]').find(node => node.dataset.action === 'toggle-action' && node.dataset.id === eventId && node.dataset.actionId === actionId); assert.ok(control); control.checked = checked; emit('change', control); },
    submit() { emit('submit', get('question-form')); },
    close() { for (const timer of timers) clearTimeout(timer); timers.clear(); },
  };
}

test('renderer covers overview, feed, company, saved, actions and every insight tab', async () => {
  const h = harness();
  try {
    await h.ready();
    assert.match(h.html(), /Ваш бизнес\. В контексте/);
    assert.match(h.html(), /Демонстрационные новости и компании/);
    assert.match(h.html('navigation'), /Бизнес-пульс/);
    for (const route of ['#feed', '#company', '#saved', '#actions', ...['analysis', 'risks', 'actions', 'sources'].map(tab => `#insight/${fixture.events[0].id}/${tab}`)]) {
      await h.go(route); assert.match(h.html(), /<h1/); assert.doesNotMatch(h.html(), /undefined|NaN/);
    }
  } finally { h.close(); }
});

test('search and filters update results while preserving the entered query', async () => {
  const h = harness({ hash: '#feed' });
  try {
    await h.ready();
    h.input('search', 'несуществующий запрос 937561');
    assert.match(h.html('feed-results'), /Подходящих событий нет/);
    assert.equal(h.get('search').value, 'несуществующий запрос 937561');
    h.action('reset-filters');
    assert.match(h.html(), /class="card news-card"/);
    h.change('category-filter', 'logistics');
    assert.match(h.html('feed-results'), /event_demo_002/);
    assert.doesNotMatch(h.html('feed-results'), /event_demo_001/);
    h.change('priority-filter', 'low');
    assert.match(h.html('feed-results'), /Подходящих событий нет/);
  } finally { h.close(); }
});

test('company and role changes alter personalization and persist through app reload', async () => {
  const h = harness({ hash: '#feed' });
  try {
    await h.ready(); const initial = h.html();
    h.change('role-select', 'ceo'); assert.notEqual(h.html(), initial);
    h.change('company-select', fixture.companies[1].id); assert.match(h.html(), /Контур/);
    assert.doesNotMatch(h.html(), /event_demo_002/);
    const restored = harness({ storage: h.storage, hash: '#feed' });
    try { await restored.ready(); assert.equal(restored.get('company-select').value, fixture.companies[1].id); assert.equal(restored.get('role-select').value, 'ceo'); } finally { restored.close(); }
  } finally { h.close(); }
});

test('saved view starts empty, adds a bookmark and restores it from local storage', async () => {
  const h = harness({ hash: '#saved' });
  try {
    await h.ready(); assert.match(h.html(), /Здесь будет важное/);
    await h.go(`#insight/${fixture.events[0].id}/analysis`); h.action('bookmark');
    await h.go('#saved'); assert.match(h.html(), /event_demo_001/);
    const restored = harness({ storage: h.storage, hash: '#saved' });
    try { await restored.ready(); assert.match(restored.html(), /event_demo_001/); } finally { restored.close(); }
  } finally { h.close(); }
});

test('source, missing bank position and prepared interpretation remain separate', async () => {
  const h = harness({ hash: `#insight/${fixture.events[0].id}/analysis` });
  try {
    await h.ready(); assert.match(h.html(), /Что произошло/); assert.match(h.html(), /позиция банка|Позиция банка/);
    assert.match(h.html(), /Подготовленный вручную пример AI-интерпретации/);
    h.action('source'); assert.equal(h.get('modal').open, true); assert.match(h.html('modal-body'), /Вымышленный учебный источник/);
    assert.ok(h.html('modal-body').includes(fixture.events[0].sources[0].text));
    h.action('close-modal'); assert.equal(h.get('modal').open, false);
  } finally { h.close(); }
});

test('tab keyboard handlers maintain selected tab, focus target and ARIA linkage', async () => {
  const h = harness({ hash: `#insight/${fixture.events[0].id}/analysis` });
  try {
    await h.ready(); h.get('tab-analysis').focus(); h.key('ArrowRight');
    assert.equal(h.get('tab-risks').getAttribute('aria-selected'), 'true');
    assert.equal(h.document.activeElement.id, 'tab-risks');
    assert.equal(h.get('insight-panel').getAttribute('aria-labelledby'), 'tab-risks');
    h.key('End'); assert.equal(h.document.activeElement.id, 'tab-sources');
    h.key('Home'); assert.equal(h.document.activeElement.id, 'tab-analysis');
  } finally { h.close(); }
});

test('skip link prevents hash navigation and targets main content without losing the current route', async () => {
  const route = `#insight/${fixture.events[0].id}/analysis`, h = harness({ hash: route });
  try {
    await h.ready(); const event = h.skip();
    assert.equal(event.defaultPrevented, true); assert.equal(h.hash(), route);
    assert.equal(h.document.activeElement.id, 'main'); assert.equal(h.get('main').scrolledIntoView, true);
  } finally { h.close(); }
});

test('file protocol uses the bundled fixture without requesting a network resource', async () => {
  const h = harness({ protocol: 'file:', fetch: async () => { throw new Error('File launch must not fetch'); } });
  try {
    await h.ready(); assert.match(h.html(), /Ваш бизнес\. В контексте/); assert.deepEqual(h.requests, []);
    await h.go('#feed'); assert.match(h.html(), /class="card news-card"/);
  } finally { h.close(); }
});

test('denied localStorage leaves working session state and discloses its temporary lifetime', async () => {
  const h = harness({ storageDenied: true, hash: `#insight/${fixture.events[0].id}/actions` });
  try {
    await h.ready(); assert.match(h.html(), /только до перезагрузки/);
    h.action('bookmark'); await h.go('#saved'); assert.match(h.html(), /event_demo_001/);
    h.action('about'); assert.match(h.html('modal-body'), /в памяти до перезагрузки/);
    assert.equal(h.storage.size, 0);
  } finally { h.close(); }
});

test('prepared and unsupported questions both disclose demo limitations', async () => {
  const h = harness({ hash: `#insight/${fixture.events[0].id}/analysis` });
  try {
    await h.ready(); h.action('suggest-question', { index: '0' }); await pause();
    assert.match(h.html('question-answer'), /ПОДГОТОВЛЕННЫЙ ДЕМО-ОТВЕТ/);
    assert.ok(h.html('question-answer').includes(fixture.events[0].suggested_questions[0].answer));
    h.input('question-input', 'Совершенно новый вопрос 12345'); h.submit(); await pause();
    assert.match(h.html('question-answer'), /AI-сервис не подключён/);
    assert.match(h.html('question-answer'), /новый вывод не сформирован/);
  } finally { h.close(); }
});

test('question drafts and answers survive tabs/bookmarks and remain isolated by role', async () => {
  const h = harness({ hash: `#insight/${fixture.events[0].id}/analysis` });
  try {
    await h.ready(); h.input('question-input', 'Мой неподготовленный вопрос'); h.tab('risks');
    assert.equal(h.get('question-input').value, 'Мой неподготовленный вопрос');
    h.action('suggest-question', { index: '0' }); await pause();
    const answer = h.html('question-answer'); h.action('bookmark');
    assert.equal(h.html('question-answer'), answer);
    assert.equal(h.get('question-input').value, fixture.events[0].suggested_questions[0].question);
    h.change('role-select', 'ceo'); assert.equal(h.get('question-input').value, ''); assert.equal(h.get('question-answer').hidden, true);
    h.change('role-select', 'financial_director'); assert.equal(h.html('question-answer'), answer);
    assert.equal(h.get('question-input').value, fixture.events[0].suggested_questions[0].question);
  } finally { h.close(); }
});

test('an in-flight question survives a tab change and resolves in the matching context', async () => {
  let release;
  const delayed = new Promise(resolve => { release = resolve; });
  const h = harness({ api: true, hash: `#insight/${fixture.events[0].id}/analysis`, fetch: async url => ({ ok: true, status: 200, json: async () => url.includes('/questions') ? delayed : clone(fixture) }) });
  try {
    await h.ready(); h.input('question-input', 'Что нужно проверить?'); h.submit(); h.tab('sources');
    assert.equal(h.get('question-submit').disabled, true); assert.match(h.html('question-answer'), /Ожидаем ответ/);
    release({ answer: 'Проверить первоисточник', is_demo: true, source_ids: [] }); await pause();
    assert.match(h.html('question-answer'), /Проверить первоисточник/); assert.equal(h.get('question-submit').disabled, false);
  } finally { release({ answer: '', is_demo: true, source_ids: [] }); h.close(); }
});

test('action completion and feedback persist and action state is isolated by company', async () => {
  const event = fixture.events[0], action = event.actions[0];
  const h = harness({ hash: `#insight/${event.id}/actions` });
  try {
    await h.ready(); h.check(event.id, action.id, true); h.action('feedback', { value: 'yes' });
    const stored = JSON.parse(h.storage.get('pulse:v3'));
    assert.equal(stored.done.length, 1); assert.equal(Object.values(stored.feedback)[0], 'yes');
    await h.go('#actions'); assert.match(h.html(), /aria-valuenow="1"/);
    h.change('company-select', fixture.companies[1].id); await h.go(`#insight/${event.id}/actions`);
    assert.doesNotMatch(h.html(), new RegExp(`data-action-id="${action.id}" checked`));
    const restored = harness({ storage: h.storage, hash: '#actions' });
    try { await restored.ready(); assert.match(restored.html(), /aria-valuenow="0"/); } finally { restored.close(); }
  } finally { h.close(); }
});

test('loading, empty, error retry and incomplete scenarios render explicitly', async () => {
  for (const [scenario, pattern] of [['loading', /Загружаем события/], ['empty', /Пока нет событий/], ['error', /Источник данных недоступен/], ['partial', /Интерпретация не подготовлена/]]) {
    const h = harness({ search: `?state=${scenario}`, hash: scenario === 'partial' ? `#insight/${fixture.events[0].id}/analysis` : '#overview' });
    try {
      await pause(); assert.match(h.html(), pattern);
      if (scenario === 'loading') { assert.equal(h.get('main').getAttribute('aria-busy'), 'true'); h.action('restore'); await h.ready(); }
      if (scenario === 'error') { h.action('reload'); await h.ready(); assert.match(h.html(), /Ваш бизнес/); }
    } finally { h.close(); }
  }
});

test('API error and malformed JSON data never silently fall back to demo analysis', async () => {
  let fail = true;
  const h = harness({ api: true, fetch: async () => ({ ok: !fail, status: fail ? 503 : 200, json: async () => clone(fixture) }) });
  try {
    await h.ready(); assert.match(h.html(), /ошибку 503/); assert.match(h.html(), /не подменяются демо/);
    fail = false; h.action('reload'); await h.ready(); assert.match(h.html(), /Ваш бизнес/);
  } finally { h.close(); }
  const malformed = harness({ api: true, data: { schema_version: '3.0', is_demo: true, events: 'invalid' } });
  try { await malformed.ready(); assert.match(malformed.html(), /Структура данных/); } finally { malformed.close(); }
});

test('optional fields render incomplete views and invalid optional collections are normalized', async () => {
  const minimal = { ...clone(fixture), events: [{ id: 'incomplete', company_ids: [fixture.companies[0].id], title: 'Неполная запись', date: '2026-10-22', priority: 'medium', facts: null, sources: {}, actions: 'wrong', ai_interpretation: { risks: 'wrong', opportunities: null, assumptions: null } }] };
  const h = harness({ data: minimal, hash: '#insight/incomplete/analysis' });
  try {
    await h.ready(); assert.match(h.html(), /Интерпретация не подготовлена/); assert.match(h.html(), /Проверенные факты пока не загружены/);
    h.tab('sources'); assert.match(h.html(), /Источники не загружены/);
    h.tab('actions'); assert.match(h.html(), /Пока нет обоснованных действий/);
    h.tab('risks'); assert.match(h.html(), /Риски не описаны/);
  } finally { h.close(); }
});

test('untrusted title/source strings are escaped and unsafe URLs cannot become source links', async () => {
  const malicious = clone(fixture);
  malicious.events[0].title = '<img src=x onerror="window.injected=true">';
  malicious.events[0].sources[0].text = '<script>window.injected=true</script>';
  malicious.events[0].sources[0].url = 'javascript:window.injected=true';
  const h = harness({ data: malicious, hash: `#insight/${fixture.events[0].id}/analysis` });
  try {
    await h.ready(); assert.match(h.html(), /&lt;img/); assert.doesNotMatch(h.html(), /<img/);
    h.action('source'); assert.match(h.html('modal-body'), /&lt;script&gt;/); assert.doesNotMatch(h.html('modal-body'), /href="javascript:/);
    assert.equal(h.context.injected, undefined);
  } finally { h.close(); }
});

test('question API requires provenance, retains failed input and accepts a labeled retry', async () => {
  let malformed = true;
  const h = harness({ api: true, hash: `#insight/${fixture.events[0].id}/analysis`, fetch: async url => ({ ok: true, status: 200, json: async () => url.includes('/questions') ? (malformed ? { answer: 'Неподписанный ответ' } : { answer: '<b>Ответ сервера</b>', is_demo: true, source_ids: [] }) : clone(fixture) }) });
  try {
    await h.ready(); h.input('question-input', 'Каких данных не хватает?'); h.submit(); await pause();
    assert.match(h.html('question-answer'), /происхождении/); assert.equal(h.get('question-input').value, 'Каких данных не хватает?');
    malformed = false; h.submit(); await pause(); assert.match(h.html('question-answer'), /ПОДГОТОВЛЕННЫЙ ДЕМО-ОТВЕТ/); assert.match(h.html('question-answer'), /&lt;b&gt;/);
    const request = h.requests.find(r => r.url.includes('/questions'));
    assert.equal(JSON.parse(request.init.body).company_id, fixture.companies[0].id);
  } finally { h.close(); }
});

test('unknown routes, foreign-company events and an empty company list show safe empty states', async () => {
  const h = harness();
  try { await h.ready(); await h.go('#missing'); assert.match(h.html(), /Страница не найдена/); await h.go('#insight/event_demo_003/analysis'); assert.match(h.html(), /Событие недоступно/); } finally { h.close(); }
  const empty = harness({ data: { schema_version: '3.0', is_demo: true, as_of: fixture.as_of, companies: [], events: [] } });
  try { await empty.ready(); assert.match(empty.html(), /Компании не найдены/); } finally { empty.close(); }
});
