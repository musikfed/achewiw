'use strict';

const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const vm = require('node:vm');

const source = readFileSync('app/static/app.js', 'utf8');

function studio(manuscripts = {}) {
  const controls = new Map();
  const element = () => ({
    children: [],
    textContent: '',
    hidden: false,
    addEventListener() {},
    append(...children) { this.children.push(...children); },
    replaceChildren(...children) { this.children = children; },
    removeAttribute(name) { delete this[name]; },
  });
  const get = id => {
    if (!controls.has(id)) controls.set(id, element());
    return controls.get(id);
  };
  const context = vm.createContext({
    document: {
      documentElement: { dataset: {} },
      querySelector: get,
      querySelectorAll: () => [],
      createElement: element,
      addEventListener() {},
    },
    window: { addEventListener() {} },
    localStorage: { getItem() {}, setItem() {} },
    setInterval() {},
    fetch: url => {
      // Keep Studio's startup requests outside this view test.
      if (url === '/api/storage/reset-epoch') return new Promise(() => {});
      const manuscript = /^\/api\/projects\/([^/]+)\/manuscript$/.exec(url);
      const payload = manuscript ? manuscripts[manuscript[1]] : [];
      return Promise.resolve({ ok: true, json: async () => payload });
    },
  });
  vm.runInContext(source, context, { filename: 'app/static/app.js' });
  return { get, run: code => vm.runInContext(code, context) };
}

test('switching books clears all previous draft PDF links', () => {
  const { get, run } = studio();
  const draftLinks = ['#bookLivePdf', '#bookPartialPdf', '#bookPartialPdfPlan'];
  const job = "{id:'draft-a',project_id:'a',model:'qwen',status:'running',book:{generated_words:184}}";
  get('#book').hidden = true;
  run("project={id:'a'}; renderFullBookJob(" + job + ')');
  for (const id of draftLinks) {
    assert.equal(get(id).hidden, false, id);
    assert.equal(get(id).href, '/api/books/jobs/draft-a/draft.pdf', id);
  }
  run("project={id:'b'}; resetBookView(); renderFullBookJob(" + job + ')');
  for (const id of draftLinks) {
    assert.equal(get(id).hidden, true, id);
    assert.equal(get(id).href, undefined, id);
  }
});

test('an empty book has no manuscript-ready message while loading or after load', async () => {
  const { get, run } = studio({
    a: {
      title: 'Written book',
      total_scenes: 1, written_scenes: 1, total_words: 3, unwritten: [],
      chapters: [{ index: 1, title: 'Chapter 1', text: 'A short chapter' }],
    },
    b: { title: 'Empty book', total_scenes: 0, written_scenes: 0, total_words: 0, unwritten: [], chapters: [] },
    c: {
      title: 'Planned book', total_scenes: 1, written_scenes: 0, total_words: 0, chapters: [],
      unwritten: [{ index: 1, title: 'Planned chapter' }],
    },
  });
  await run("project={id:'a'}; renderBook()");
  assert.match(get('#bookWarning').textContent, /Все части содержат текст/);
  assert.equal(get('#bookDocx').hidden, false);

  const emptyLoading = run("project={id:'b'}; renderBook()");
  assert.equal(get('#bookWarning').textContent, '');
  assert.equal(get('#bookDocx').hidden, true);
  await emptyLoading;
  assert.equal(get('#bookWarning').textContent, '');
  const emptyHint = get('#bookPreview').children.find(child => child.className === 'book-empty');
  assert.match(emptyHint.textContent, /Пока нет ни одного раздела/);
  assert.equal(get('#bookPdf').hidden, true);

  await run("project={id:'c'}; renderBook()");
  assert.match(get('#bookWarning').textContent, /Ещё не написаны части: 1\. Planned chapter/);
  assert.equal(get('#bookDocx').hidden, true);
});
