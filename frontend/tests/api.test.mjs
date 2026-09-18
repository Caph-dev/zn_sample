import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {test} from 'node:test';
import ts from 'typescript';

// These modules have only type imports, so they need no browser or test bundler.
async function importTypeScriptModule(relativePath) {
  const source = await readFile(new URL(relativePath, import.meta.url), 'utf8');
  const {outputText} = ts.transpileModule(source, {
    compilerOptions: {module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022},
  });
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);
}

const {createPreview, fetchJson, getOrderBackfillCandidates, saveRuleDraft, startOrderBackfill} =
  await importTypeScriptModule('../src/api.ts');
const {buildStandardRule, hasOutdatedSkuEvidence, rulesEqual, summaryRows, validateDraft} = await importTypeScriptModule('../src/ruleModel.ts');
const {readRuleMemory, writeRuleMemory} = await importTypeScriptModule('../src/ruleMemory.ts');
const {readExecutionLimit, writeExecutionLimit} = await importTypeScriptModule('../src/executionLimitMemory.ts');

test('order-number backfill posts one confirmed write and scans candidates read-only', async (context) => {
  const requests = [];
  context.mock.method(globalThis, 'fetch', async (path, request) => {
    requests.push({path, method: request?.method ?? 'GET', body: request?.body ?? null});
    if (path === '/api/auto-approval/order-backfill/candidates') {
      return Response.json({person: '王良希（技术）', lookback_hours: 72, total: 0, rows: []});
    }
    return Response.json({job_id: 'job-1', store_id: 'store-1', limit: 0});
  });
  const candidates = await getOrderBackfillCandidates();
  assert.equal(candidates.person, '王良希（技术）');
  await startOrderBackfill({store_id: 'store-1', limit: 0, confirmation: 'y'});
  assert.deepEqual(requests, [
    {path: '/api/auto-approval/order-backfill/candidates', method: 'GET', body: null},
    {
      path: '/api/auto-approval/order-backfill',
      method: 'POST',
      body: JSON.stringify({store_id: 'store-1', limit: 0, confirmation: 'y'}),
    },
  ]);
});

test('order-number backfill never retries a write when its response is lost', async (context) => {
  const fetchMock = context.mock.method(globalThis, 'fetch', async () => {
    throw new TypeError('Connection lost after submission');
  });
  await assert.rejects(
    startOrderBackfill({store_id: 'store-1', limit: 0, confirmation: 'y'}),
    /Connection lost/,
  );
  assert.equal(fetchMock.mock.callCount(), 1);
});

test('execution limit defaults to 20 and immediately remembers valid edits', () => {
  const values = new Map();
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };
  assert.equal(readExecutionLimit(storage), 20);
  for (const limit of [1, 35, 1000]) {
    assert.equal(writeExecutionLimit(storage, limit), true);
    assert.equal(readExecutionLimit(storage), limit);
  }
  for (const invalidLimit of [0, -1, 1.5, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1]) {
    assert.equal(writeExecutionLimit(storage, invalidLimit), false);
    assert.equal(readExecutionLimit(storage), 1000);
  }
});

test('invalid limit memory and unavailable storage safely fall back to 20', () => {
  for (const storedValue of ['{', 'null', '"35"', '{}', '0', '-1', '2.5', '1e999']) {
    assert.equal(readExecutionLimit({getItem: () => storedValue}), 20);
  }
  const blockedStorage = {
    getItem() { throw new Error('Storage denied'); },
    setItem() { throw new Error('Storage denied'); },
  };
  assert.equal(readExecutionLimit(blockedStorage), 20);
  assert.equal(writeExecutionLimit(blockedStorage, 35), false);
});

test('B005 fixed SKU rule appears only when its product is selected', () => {
  const rule = buildStandardRule(null);
  assert.ok(summaryRows(rule).some((row) => row.value.includes('6PCS')));
  rule.product_ids = ['other-product'];
  assert.ok(!summaryRows(rule).some((row) => row.value.includes('6PCS')));
});

test('old B005 results cannot remain executable after the SKU rule is introduced', () => {
  const row = {
    product_id: '1732414717062320994', custom_eligible: true,
    metrics: {sku_desc: '3PCS,L'}, checks: [],
  };
  assert.equal(hasOutdatedSkuEvidence({rows: [row]}), true);
  row.checks = [{key: 'b005_sku', source: 'application.sku_desc', status: 'passed', value: '3PCS,L'}];
  assert.equal(hasOutdatedSkuEvidence({rows: [row]}), false);
  row.metrics.sku_desc = '6PCS,L';
  assert.equal(hasOutdatedSkuEvidence({rows: [row]}), true);
  row.custom_eligible = false;
  row.checks[0].status = 'failed';
  assert.equal(hasOutdatedSkuEvidence({rows: [row]}), false);
  assert.equal(hasOutdatedSkuEvidence({rows: [{...row, product_id: 'other-product', checks: []}]}), false);
});

test('form memory retains disabled values, hidden side selections and incomplete edits', () => {
  const rule = buildStandardRule(null);
  rule.product_ids.push('another-product');
  rule.basic.fulfillment.min = 85;
  rule.basic.followers = {enabled: false, min: 4321, values: []};
  rule.basic.aov = {enabled: false, min: 12, max: 23, values: []};
  rule.basic.gmv.min = null;
  rule.video_live.enabled = false;
  rule.video_live.video.gpm = 17;
  rule.video_live.live.enabled = false;
  rule.content.enabled = false;
  const memory = {rule, displayMode: 'custom'};
  const values = new Map();
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };
  assert.equal(writeRuleMemory(storage, memory), true);
  assert.deepEqual(readRuleMemory(storage), memory);
  assert.notDeepEqual(validateDraft(readRuleMemory(storage).rule, null), []);

  memory.displayMode = 'standard';
  writeRuleMemory(storage, memory);
  assert.deepEqual(readRuleMemory(storage), memory);
});

test('corrupt or incompatible memory is ignored and blocked storage does not crash', () => {
  const rule = buildStandardRule(null);
  for (const storedValue of [
    '{', 'null', '{}',
    JSON.stringify({rule: {...rule, basic: {}}, displayMode: 'custom'}),
    JSON.stringify({rule: {...rule, schema_version: 2}, displayMode: 'custom'}),
    JSON.stringify({rule: {...rule, video_live: null}, displayMode: 'custom'}),
  ]) {
    assert.equal(readRuleMemory({getItem: () => storedValue}), null);
  }
  const blockedStorage = {
    getItem() { throw new Error('Storage denied'); },
    setItem() { throw new Error('Storage denied'); },
  };
  assert.equal(readRuleMemory(blockedStorage), null);
  assert.equal(writeRuleMemory(blockedStorage, {rule, displayMode: 'custom'}), false);
});

for (const operation of ['preview', 'draft']) {
  test(`${operation} disables hidden video/live sides without changing the form`, async (context) => {
    const rule = buildStandardRule(null);
    for (const [key, condition] of Object.entries(rule.basic)) {
      condition.enabled = key === 'fulfillment' || key === 'categories';
    }
    rule.basic.fulfillment.min = 85;
    rule.video_live.enabled = false;
    rule.content.enabled = false;
    const originalRule = structuredClone(rule);
    assert.deepEqual(validateDraft(rule, null), []);

    let submittedRule;
    context.mock.method(globalThis, 'fetch', async (path, request) => {
      const payload = JSON.parse(request.body);
      submittedRule = payload.rule;
      assert.equal(request.method, operation === 'preview' ? 'POST' : 'PUT');
      assert.equal(path, `/api/auto-approval/${operation === 'preview' ? 'previews' : 'rule-draft'}`);
      if (operation === 'preview') {
        assert.equal(payload.store_id, 'test-store');
      }
      return Response.json({});
    });

    if (operation === 'preview') {
      await createPreview(rule, 'test-store');
    } else {
      await saveRuleDraft(rule);
    }

    const expectedRule = structuredClone(originalRule);
    expectedRule.video_live.video.enabled = false;
    expectedRule.video_live.live.enabled = false;
    assert.deepEqual(submittedRule, expectedRule);
    assert.deepEqual(rule, originalRule);
    assert.equal(rulesEqual(rule, submittedRule), true);

    // Re-enabling the group still submits the user's original side selections.
    rule.video_live.enabled = true;
    if (operation === 'preview') {
      await createPreview(rule, 'test-store');
    } else {
      await saveRuleDraft(rule);
    }
    assert.deepEqual(submittedRule.video_live, rule.video_live);
  });
}

const errorCases = [
  {
    name: 'FastAPI nested business error',
    body: {detail: {code: 'invalid-logic', message: 'Group is disabled but a side is enabled'}},
    code: 'invalid-logic',
    message: 'Group is disabled but a side is enabled',
  },
  {
    name: 'top-level business error',
    body: {code: 'store-busy', message: 'Store is busy'},
    code: 'store-busy',
    message: 'Store is busy',
  },
  {
    name: 'FastAPI string detail',
    body: {detail: 'invalid-origin'},
    code: 'http-error',
    message: 'invalid-origin',
  },
];

for (const errorCase of errorCases) {
  test(`fetchJson preserves ${errorCase.name}`, async (context) => {
    context.mock.method(globalThis, 'fetch', async () => Response.json(errorCase.body, {status: 400}));
    await assert.rejects(fetchJson('/test'), {
      code: errorCase.code,
      message: errorCase.message,
      status: 400,
    });
  });
}

test('fetchJson keeps an HTTP fallback for non-JSON errors', async (context) => {
  context.mock.method(globalThis, 'fetch', async () => new Response('Unavailable', {status: 503}));
  await assert.rejects(fetchJson('/test'), (error) => {
    assert.equal(error.code, 'http-error');
    assert.equal(error.status, 503);
    assert.match(error.message, /503/);
    return true;
  });
});
