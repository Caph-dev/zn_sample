import assert from 'node:assert/strict';
import {after, test} from 'node:test';
import {fileURLToPath} from 'node:url';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';

// Vite is an existing dependency. Middleware mode does not listen on a port.
const loader = await createServer({
  configFile: false,
  root: fileURLToPath(new URL('../../', import.meta.url)),
  esbuild: {jsx: 'automatic'},
  server: {middlewareMode: true, hmr: false, watch: null},
  appType: 'custom',
});
after(() => loader.close());
const {
  TargetCleanupPage, cleanupConfirmationDescription, readCleanupRequest,
  readCleanupReceipt, reduceCleanupView, writeCleanupRequest,
} = await loader.ssrLoadModule('/frontend/src/console/pages/TargetCleanupPage.tsx');

function syntheticBatch(overrides = {}) {
  return {
    batch_id: 'batch-4', months: 4, store_id: 'bound-store', store_name: 'Frozen store',
    shop_id: 'shop-1', shop_region: 'US',
    frozen: {months: 4, cutoff: '2026-06-05', run_date: '2026-10-05',
      started_at: '2026-10-04T23:00:00+00:00', timezone_name: 'Frozen zone', offset_minutes: 60},
    preview_status: 'completed', execute_status: 'unclaimed',
    preview_job_id: 'preview-job', execute_job_id: '', scan_complete: true,
    stop_reason: 'first-page', pages_scanned: 1, scan_count: 2, candidate_count: 2, nonzero_count: 1,
    error_code: '', error_summary: '', created_at: '2026-10-04T23:00:00+00:00',
    expires_at: '2026-10-05T00:00:00+00:00',
    items: [{invitation_id: 'invite-1', name: 'Frozen plan', last_modified: 'Jun 1, 2026',
      modified_date: '2026-06-01', accepted_count: 3, promoted_count: 1,
      reason: 'before-cutoff', status: 'pending', action: '', summary: ''}],
    counts: {pending: 2, attempting: 0, submitted: 0, skipped: 0, failed: 0, uncertain: 0, not_processed: 0},
    offset: 0, limit: 100, total: 2, can_execute: true, execute_block_reason: '',
    jobs: {preview: {job_id: 'preview-job', status: 'succeeded'}, execute: {job_id: '', status: ''}},
    downloads: {scan_csv: '/scan.csv', candidates_csv: '/candidates.csv'}, results_csv_ready: false,
    ...overrides,
  };
}

function renderBatch(batch, readError = '') {
  return renderToStaticMarkup(createElement(TargetCleanupPage, {data: {
    database_ready: true, default_months: 4, prepare_href: '/prepare',
    batch_id: batch?.batch_id || '', batch, read_error: readError,
    recent_batches: {batches: batch ? [batch] : [], total: batch ? 1 : 0, offset: 0, limit: 20},
  }}));
}

test('switching months/batches or starting another preview clears the old executable list', () => {
  const previous = syntheticBatch();
  const state = {batchId: previous.batch_id, offset: 0, revision: 2, batch: previous, loading: false, error: ''};
  for (const nextBatchId of ['', 'batch-2']) {
    const next = reduceCleanupView(state, {type: 'select', batchId: nextBatchId});
    assert.equal(next.batch, null);
    assert.equal(next.revision, 3);
    assert.equal(reduceCleanupView(next, {type: 'loaded', revision: 2, batch: previous}), next);
    assert.equal(reduceCleanupView(next, {type: 'failed', revision: 2, message: 'Old GET failed'}), next);
  }
});

test('late candidate page responses cannot replace the newly selected offset', () => {
  const batch = syntheticBatch();
  const state = {batchId: batch.batch_id, offset: 0, revision: 1, batch, loading: false, error: ''};
  const next = reduceCleanupView(state, {type: 'select', batchId: batch.batch_id, offset: 100});
  assert.equal(reduceCleanupView(next, {type: 'loaded', revision: next.revision, batch}), next);
  const loaded = reduceCleanupView(next, {type: 'loaded', revision: next.revision, batch: {...batch, offset: 100}});
  assert.equal(loaded.batch.offset, 100);
});

test('read failures retain the last confirmed batch and ID but remove the executable action', () => {
  const batch = syntheticBatch();
  const state = {batchId: batch.batch_id, offset: 0, revision: 1, batch, loading: false, error: ''};
  const refreshing = reduceCleanupView(state, {type: 'refresh'});
  const failed = reduceCleanupView(refreshing, {type: 'failed', revision: refreshing.revision, message: 'Read failed'});
  assert.equal(failed.batch, batch);
  assert.equal(failed.batchId, batch.batch_id);
  const markup = renderBatch(failed.batch, failed.error);
  assert.ok(!markup.includes('data-target-cleanup-form="execute"'));
  assert.ok(markup.includes('Read failed'));
});

test('per-month and per-batch request identities survive lost responses and reloads', () => {
  const values = new Map();
  const storage = {getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value)};
  for (const slot of ['preview:4', 'preview:2', 'execute:batch-4']) {
    assert.equal(readCleanupRequest(storage, slot), null);
    const request = {key: `stable-${slot}`, attempted: true, confirmedBatchId: ''};
    writeCleanupRequest(storage, slot, request);
    assert.deepEqual(readCleanupRequest(storage, slot), request);
    const confirmed = {...request, confirmedBatchId: 'batch-4'};
    writeCleanupRequest(storage, slot, confirmed);
    assert.equal(readCleanupRequest(storage, slot).key, request.key);
  }
  assert.notEqual(readCleanupRequest(storage, 'preview:2').key, readCleanupRequest(storage, 'preview:4').key);
});

test('invalid or unavailable request memory does not synthesize replacement keys', () => {
  for (const invalid of ['{', 'null', '{}', '{"key":"","attempted":true,"confirmedBatchId":""}']) {
    assert.throws(() => readCleanupRequest({getItem: () => invalid}, 'preview:4'));
  }
  const unavailable = {getItem() {throw new Error('Denied');}, setItem() {throw new Error('Denied');}};
  assert.throws(() => readCleanupRequest(unavailable, 'preview:4'), /Denied/);
  assert.throws(() => writeCleanupRequest(unavailable, 'preview:4', {}), /Denied/);
});

test('receipt recovery is GET-only, bounded, uses the original key, and never retries on errors', async (context) => {
  const requests = [];
  context.mock.method(globalThis, 'fetch', async (path, options) => {
    requests.push({path, options});
    throw new TypeError('Lost read response');
  });
  const path = '/api/target-cleanup/batches?limit=20&offset=0&idempotency_key=original-key';
  await assert.rejects(readCleanupReceipt('preview:4', 'original-key'), /Lost read response/);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].path, path);
  assert.equal(requests[0].options.method, undefined);
  assert.equal(requests[0].options.body, undefined);
  assert.equal(requests[0].options.cache, 'no-store');
});

test('recovery refuses the wrong month, wrong execution batch, or a preview-only receipt', async (context) => {
  let recoveredBatch = syntheticBatch({months: 2, execute_job_id: null});
  context.mock.method(globalThis, 'fetch', async () => Response.json({batches: [recoveredBatch]}));
  assert.equal(await readCleanupReceipt('preview:4', 'same-key'), undefined);
  assert.equal((await readCleanupReceipt('preview:2', 'same-key')).months, 2);
  assert.equal(await readCleanupReceipt('execute:batch-4', 'same-key'), undefined);
  recoveredBatch = {...recoveredBatch, execute_job_id: 'execution-job'};
  assert.equal(await readCleanupReceipt('execute:other-batch', 'same-key'), undefined);
  assert.equal((await readCleanupReceipt('execute:batch-4', 'same-key')).execute_job_id, 'execution-job');
});

test('SSR uses frozen evidence and a single exact FormData contract with the existing y modal', () => {
  const batch = syntheticBatch();
  const markup = renderBatch(batch);
  assert.ok(markup.includes('Frozen zone'));
  assert.ok(markup.includes('Jun 1, 2026'));
  assert.ok(markup.includes('2026-06-01'));
  assert.ok(markup.includes('2026-06-05'));
  const forms = [...markup.matchAll(/<form\b[^>]*>([\s\S]*?)<\/form>/g)];
  assert.equal(forms.length, 3);
  for (const form of forms) {
    const names = [...form[1].matchAll(/<input\b[^>]*name="([^"]+)"/g)].map((match) => match[1]);
    assert.deepEqual(names, form[0].includes('data-target-cleanup-form="preview"')
      ? ['months', 'idempotency_key'] : ['idempotency_key']);
  }
  assert.ok(markup.includes('data-confirm-token="y"'));
  const description = cleanupConfirmationDescription(batch);
  for (const evidence of ['Frozen store', 'bound-store', '4 个自然月', '2026-06-05', '全部 2 条', '1 条', '不是默认只执行 1 条', '不可脚本撤销']) {
    assert.ok(description.includes(evidence));
  }
});

test('server can_execute alone controls eligibility; incomplete/empty/expired/claimed views hide execution', () => {
  for (const reason of ['incomplete-preview', 'empty-preview', 'expired-preview', 'batch-already-consumed', 'store-busy']) {
    const markup = renderBatch(syntheticBatch({can_execute: false, execute_block_reason: reason}));
    assert.ok(!markup.includes('data-target-cleanup-form="execute"'), reason);
    assert.ok(markup.includes('/scan.csv') && markup.includes('/candidates.csv'));
  }
  // Even expired-looking client timestamps cannot override a server eligibility decision.
  assert.ok(renderBatch(syntheticBatch()).includes('data-target-cleanup-form="execute"'));
});

test('partial and uncertain results keep task/report access without claiming final platform success', () => {
  for (const status of ['partial', 'needs_review', 'running']) {
    const batch = syntheticBatch({execute_status: status, can_execute: false,
      execute_block_reason: 'batch-already-consumed',
      counts: {submitted: 1, skipped: 0, failed: 0, uncertain: 1, not_processed: 0, pending: 0, attempting: 0},
      jobs: {preview: {job_id: 'preview-job', status: 'succeeded'}, execute: {job_id: 'execute-job', status: 'failed'}},
      downloads: {scan_csv: '/scan.csv', candidates_csv: '/candidates.csv', results_csv: '/old-results.csv'},
      results_csv_ready: false});
    const markup = renderBatch(batch);
    assert.ok(markup.includes('/jobs/execute-job'));
    assert.ok(markup.includes('取消操作已提交'));
    assert.ok(markup.includes('不是平台最终确认取消'));
    assert.ok(!markup.includes('/old-results.csv'));
    assert.ok(!markup.includes('data-target-cleanup-form="execute"'));
  }
});
