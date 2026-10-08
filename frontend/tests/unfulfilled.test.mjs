import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {after, test} from 'node:test';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';
import ts from 'typescript';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';

const loader = await createServer({
  configFile: false, root: fileURLToPath(new URL('../../', import.meta.url)),
  esbuild: {jsx: 'automatic'}, optimizeDeps: {noDiscovery: true, include: []},
  server: {middlewareMode: true, hmr: false, ws: false, watch: null}, appType: 'custom',
});
after(() => loader.close());
const {FollowupsPage, UnfulfilledWriteForm, UnfulfilledJobNotice, selectUnfulfilledRows,
  unfulfilledExecutionCount, unfulfilledConfirmationDescription, readLocalFollowupsData} =
  await loader.ssrLoadModule('/frontend/src/console/pages/FollowupsPage.tsx');
const appSource = await readFile(new URL('../../assistant/web/static/app.js', import.meta.url), 'utf8');
const pageSource = await readFile(new URL('../src/console/pages/FollowupsPage.tsx', import.meta.url), 'utf8');

const candidates = Array.from({length: 5}, (_, index) => ({
  task_id: index + 1, store_id: 'store-a', store_name: '本地甲店', creator_name: `creator-${index + 1}`,
  sample_product: 'B005', record_id: '', delivered_on: '2026-09-20', days_since_delivery: 18,
  scheduled_for: '2026-10-05', send_result: '', feishu_cooperation_status: '待发布',
}));

function renderWriteForm(overrides = {}) {
  return renderToStaticMarkup(createElement(UnfulfilledWriteForm, {
    candidates, storeId: 'store-a', selectedIds: [1, 2, 3, 4, 5], executeLimit: 1,
    blocked: false, onLimitChange() {}, ...overrides,
  }));
}

test('five selected rows with limit one truthfully display one and submit only the fixed fields', () => {
  const markup = renderWriteForm();
  assert.ok(markup.includes('已选 5 条'));
  assert.ok(markup.includes('写飞书未发布（本次 1 条）'));
  assert.ok(markup.includes('data-confirm-token="y"'));
  assert.ok(markup.includes('action="/api/jobs/followups/unfulfilled"'));
  const fields = [...markup.matchAll(/<input\b[^>]*name="([^"]+)"[^>]*value="([^"]+)"/g)]
    .map((match) => [match[1], match[2]]);
  assert.deepEqual(fields, [['task_ids', '1'], ['task_ids', '2'], ['task_ids', '3'],
    ['task_ids', '4'], ['task_ids', '5'], ['execute_limit', '1']]);
  assert.ok(!markup.includes('name="store_id"'));
  const description = unfulfilledConfirmationDescription('本地甲店', 'store-a', 1);
  for (const text of ['本地甲店', 'store-a', '1 条', '只写飞书合作状态', '不发私信', '先同步物流']) {
    assert.ok(description.includes(text));
  }
});

test('selection cannot include unknown, ineligible or cross-store IDs and has no implicit default store', () => {
  const allRows = [...candidates, {...candidates[0], task_id: 9, store_id: 'store-b'}];
  assert.deepEqual(selectUnfulfilledRows(allRows, '', [1, 9]), []);
  assert.deepEqual(selectUnfulfilledRows(allRows, 'store-a', [1, 9, 999]).map((row) => row.task_id), [1]);
  for (const executeLimit of [0, -1, 1.5, NaN, Infinity]) {
    assert.equal(unfulfilledExecutionCount(5, executeLimit), 0);
  }
  assert.equal(unfulfilledExecutionCount(5, 2), 2);
  for (const overrides of [{candidates: []}, {selectedIds: []}, {storeId: ''}, {blocked: true}]) {
    const markup = renderWriteForm(overrides);
    assert.match(markup, /<button[^>]*type="submit"[^>]*disabled/);
  }
});

test('stage operations remain discoverable with zero candidates without inventing D10 batch actions', () => {
  const data = {rows: [], operator_groups: [], unfulfilled: {candidates: [], jobs: []},
    filters: {stage: 'unfulfilled', status: '', language: '', platform_status: ''},
    filter_options: {stages: [], statuses: [], languages: [], platform_statuses: []}};
  const markup = renderToStaticMarkup(createElement(FollowupsPage, {data}));
  assert.ok(markup.includes('阶段操作'));
  assert.ok(markup.includes('查看 D+10 待办'));
  assert.ok(markup.includes('查看 D+15 待办（0 条）'));
  assert.ok(markup.includes('暂无可处理待办'));
  assert.ok(markup.includes('写飞书未发布（本次 0 条）'));
  const navigationButtons = [...markup.matchAll(/<button\b[^>]*>[\s\S]*?<\/button>/g)]
    .map((match) => match[0]).filter((button) => button.includes('查看 D+'));
  assert.equal(navigationButtons.length, 2);
  assert.ok(navigationButtons.every((button) => button.includes('data-variant="secondary"')));
  assert.match(markup, /<button[^>]*type="submit"[^>]*disabled/);
  assert.ok(!markup.includes('批量标记已出名单'));
});

test('deduplicated receipt shows the original scope and failed jobs retain their result entry', () => {
  for (const status of ['pending', 'running', 'failed']) {
    const markup = renderToStaticMarkup(createElement(UnfulfilledJobNotice, {job: {
      job_id: 'original-job', task_ids: [41], store_id: 'original-store', execute_limit: 1,
      count: 1, deduplicated: true, status,
    }}));
    assert.ok(markup.includes('已复用原任务，新选择未另行执行'));
    assert.ok(markup.includes('original-store'));
    assert.ok(markup.includes('实际任务 1 条'));
    assert.ok(markup.includes('41'));
    assert.ok(markup.includes('/jobs/original-job'));
    assert.ok(markup.includes(status === 'failed' ? '查看任务与逐行结果' : '正在处理，查看任务'));
  }
});

class SyntheticElement {
  constructor(tagName) {this.tagName = tagName; this.children = []; this.textContent = '';}
  append(...children) {this.children.push(...children);}
  replaceChildren(...children) {this.children = children;}
}

function loadApp({confirm = 'y', failedPost = false} = {}) {
  const requests = [];
  const dispatched = [];
  const callbacks = new Map();
  let confirmations = 0;
  const document = {
    querySelector() {return null;}, querySelectorAll() {return [];},
    createElement: (tag) => new SyntheticElement(tag), createTextNode: (text) => ({textContent: text}),
    addEventListener: (name, callback) => callbacks.set(name, callback),
    dispatchEvent: (event) => {dispatched.push(event); callbacks.get(event.type)?.(event);},
  };
  class SyntheticFormData {
    constructor(form) {this.fields = [...form.fields];}
    set(name, value) {this.fields = this.fields.filter(([key]) => key !== name); this.fields.push([name, value]);}
  }
  const context = vm.createContext({document, URL, Intl, console,
    window: {location: {origin: 'http://local.invalid'},
      prompt() {confirmations += 1; return confirm;}, setInterval() {return 1;}, clearInterval() {}},
    CustomEvent: class {constructor(type, {detail}) {this.type = type; this.detail = detail;}},
    FormData: SyntheticFormData, HTMLFormElement: class {},
    EventSource: class {addEventListener() {} close() {}},
    fetch: async (url, options) => {
      requests.push({url, options});
      if (options?.method === 'POST') {
        if (failedPost) throw new Error('Lost write response');
        return {ok: true, json: async () => ({job_id: 'frozen-job', deduplicated: true,
          task_ids: [41], store_id: 'original-store', execute_limit: 1, count: 1})};
      }
      if (url.endsWith('/events')) return {ok: true, json: async () => ({events: []})};
      return {ok: true, json: async () => ({id: 'frozen-job', job_type: 'followup_unfulfilled_write',
        status: 'succeeded', progress_total: 1, progress_current: 1})};
    },
  });
  vm.runInContext(appSource.replace(/\}\)\(\);\s*$/, 'globalThis.testHooks = {submitJobForm, renderJobResult};})();'), context);
  return {context, requests, dispatched, get confirmations() {return confirmations;}};
}

test('document delegation confirms once, POSTs once and publishes the server-frozen dedup receipt', async () => {
  const runtime = loadApp();
  const button = {disabled: false};
  const form = {action: '/api/jobs/followups/unfulfilled', method: 'post',
    dataset: {confirmToken: 'y', confirmDescription: 'Only one actual task'},
    fields: [['task_ids', '1'], ['task_ids', '2'], ['execute_limit', '1']], querySelector() {return button;}};
  const event = {preventDefault() {}};
  await Promise.all([runtime.context.testHooks.submitJobForm(form, event),
    runtime.context.testHooks.submitJobForm(form, event)]);
  assert.equal(runtime.confirmations, 1);
  const writes = runtime.requests.filter((request) => request.options?.method === 'POST');
  assert.equal(writes.length, 1);
  assert.deepEqual(writes[0].options.body.fields,
    [['task_ids', '1'], ['task_ids', '2'], ['execute_limit', '1'], ['confirmation', 'y']]);
  const created = runtime.dispatched.find((entry) => entry.type === 'assistant:job-created');
  assert.equal(created.detail.payload.task_ids[0], 41);
  assert.equal(created.detail.payload.store_id, 'original-store');
  assert.equal(created.detail.payload.deduplicated, true);
});

test('cancelled confirmation performs no POST and a lost response is not retried', async () => {
  for (const options of [{confirm: null}, {failedPost: true}]) {
    const runtime = loadApp(options);
    const button = {disabled: false};
    const form = {action: '/api/jobs/followups/unfulfilled', method: 'post', fields: [],
      dataset: {confirmToken: 'y', submitDisabled: 'true'}, querySelector() {return button;}};
    await runtime.context.testHooks.submitJobForm(form, {preventDefault() {}});
    assert.equal(runtime.requests.filter((request) => request.options?.method === 'POST').length,
      options.confirm === null ? 0 : 1);
    assert.equal(button.disabled, true);
    if (options.failedPost) assert.ok(runtime.dispatched.some((entry) => entry.type === 'assistant:job-submit-failed'));
  }
});

test('failed partial batches retain successful rows, unknown rows and both report downloads', () => {
  const runtime = loadApp();
  const target = new SyntheticElement('section');
  runtime.context.testHooks.renderJobResult(target, {id: 'partial-job', job_type: 'followup_unfulfilled_write',
    status: 'failed', error_summary: 'Unknown write, stopped', result_summary: JSON.stringify({
      store_id: 'store-a', task_ids: [1, 2, 3], counts: {written: 1, 'write-unknown': 1, 'not-processed': 1},
      rows: [{task_id: 1, creator_name: 'first', result: 'written'},
        {task_id: 2, result: 'write-unknown'}, {task_id: 3, result: 'not-processed'}],
      json_path: '/exports/partial-job_result.json', csv_path: '/exports/partial-job_result.csv',
      backup_path: '/exports/partial-job_pre_execute.json',
    })});
  const elements = [];
  function collect(element) {elements.push(element); for (const child of element.children || []) collect(child);}
  collect(target);
  const text = elements.map((element) => element.textContent).join(' ');
  assert.ok(text.includes('已写未发布'));
  assert.ok(text.includes('写入结果未知'));
  assert.ok(text.includes('未处理'));
  assert.ok(text.includes('Unknown write, stopped'));
  const links = elements.filter((element) => element.tagName === 'a');
  assert.ok(links.some((link) => link.href === '/jobs/partial-job'));
  for (const path of ['/exports/partial-job_result.json', '/exports/partial-job_result.csv',
    '/exports/partial-job_pre_execute.json']) {
    assert.ok(links.some((link) => new URL(link.href, 'http://local.invalid').searchParams.get('path') === path));
  }
});

test('artifact failure without report links still renders database row evidence and never retries', () => {
  const runtime = loadApp();
  const target = new SyntheticElement('section');
  runtime.context.testHooks.renderJobResult(target, {id: 'artifact-failed-job',
    job_type: 'followup_unfulfilled_write', status: 'failed', error_summary: 'record-artifact-failure',
    result_summary: JSON.stringify({store_id: 'store-a', task_ids: [1, 2],
      json_path: '', csv_path: '', backup_path: '', counts: {written: 1, 'not-processed': 1},
      rows: [{task_id: 1, creator_name: 'confirmed-first', result: 'written'},
        {task_id: 2, creator_name: 'remaining-second', result: 'not-processed'}],
    })});
  const elements = [];
  function collect(element) {elements.push(element); for (const child of element.children || []) collect(child);}
  collect(target);
  const text = elements.map((element) => element.textContent).join(' ');
  for (const evidence of ['record-artifact-failure', 'confirmed-first', '已写未发布',
    'remaining-second', '未处理', '逐行结果保留在本地任务记录中', '不要自动重试写入']) {
    assert.ok(text.includes(evidence), evidence);
  }
  const links = elements.filter((element) => element.tagName === 'a');
  assert.ok(links.some((link) => link.href === '/jobs/artifact-failed-job'));
  assert.ok(!links.some((link) => link.href.includes('/api/exports/download')));
  assert.equal(runtime.requests.length, 0);
});

test('completion refreshes candidates, counts and the generic filtered table while retaining the frozen receipt', async (context) => {
  const requests = [];
  const refreshedPayload = {rows: [{id: 41, action_label: '已完成'}],
    unfulfilled: {candidates: [], jobs: [{job_id: 'original-job', store_id: 'original-store',
      status: 'failed', task_ids: [41], execute_limit: 1, deduplicated: false}]}};
  context.mock.method(globalThis, 'fetch', async (path, options) => {
    requests.push({path, options});
    return Response.json(refreshedPayload);
  });
  // Exercise the real hook/effect body with synthetic state, without a browser or another framework.
  const effectSource = pageSource.slice(pageSource.indexOf('function StageOperations('),
    pageSource.indexOf('  const stores = ')) + '\n}';
  const {outputText} = ts.transpileModule(effectSource, {
    compilerOptions: {target: ts.ScriptTarget.ES2022},
  });
  const listeners = new Map();
  const stateValues = [];
  let effect;
  const refreshedRows = [];
  const hookContext = vm.createContext({AbortController, readLocalFollowupsData,
    ACTIVE_JOB_STATUSES: new Set(['pending', 'running']),
    window: {location: {search: '?stage=unfulfilled&status=pending'}, setInterval() {return 1;}, clearInterval() {}},
    document: {addEventListener: (name, callback) => listeners.set(name, callback), removeEventListener() {}},
    useState(initialValue) {
      const index = stateValues.length;
      stateValues.push(initialValue);
      return [initialValue, (value) => {
        stateValues[index] = typeof value === 'function' ? value(stateValues[index]) : value;
      }];
    },
    useEffect(callback) {effect = callback;},
  });
  vm.runInContext(`${outputText}\nglobalThis.mountStageOperations = StageOperations;`, hookContext);
  hookContext.mountStageOperations({initial: {candidates, jobs: []}, showCandidates: true,
    onRowsRefresh: (rows) => refreshedRows.push(rows)});
  const cleanup = effect();
  listeners.get('assistant:job-created')({detail: {form: {matches() {return true;}},
    payload: {job_id: 'original-job', task_ids: [41], store_id: 'original-store',
      execute_limit: 1, processed_count: 1, deduplicated: true}}});
  listeners.get('assistant:job-complete')({detail: {id: 'original-job',
    job_type: 'followup_unfulfilled_write', status: 'failed'}});
  await new Promise(setImmediate);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].path, '/followups/unfulfilled/data?stage=unfulfilled&status=pending');
  assert.equal(requests[0].options.cache, 'no-store');
  assert.equal(requests[0].options.method, undefined);
  assert.deepEqual(stateValues[0].candidates, []);
  assert.deepEqual(refreshedRows, [refreshedPayload.rows]);
  assert.equal(stateValues[6].deduplicated, true);
  assert.equal(stateValues[6].task_ids[0], 41);
  assert.equal(stateValues[6].store_id, 'original-store');
  assert.equal(stateValues[6].status, 'failed');
  assert.ok(pageSource.includes('data={rows as unknown as Record<string, unknown>[]}'));
  cleanup();
});
