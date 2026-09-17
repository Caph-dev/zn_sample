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

const {createPreview, fetchJson, saveRuleDraft} = await importTypeScriptModule('../src/api.ts');
const {buildStandardRule, rulesEqual, validateDraft} = await importTypeScriptModule('../src/ruleModel.ts');
const {readRuleMemory, writeRuleMemory} = await importTypeScriptModule('../src/ruleMemory.ts');

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
