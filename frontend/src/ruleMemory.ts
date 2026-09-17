import type {RuleDraft} from './types';

const RULE_MEMORY_KEY = 'zn-sample:auto-approval:rule-form:v1';
const BASIC_KEYS = ['followers', 'gmv', 'units', 'gpm', 'aov', 'fulfillment', 'female_pct', 'categories'];

export interface RuleMemory {
  rule: RuleDraft;
  displayMode: 'standard' | 'custom';
}

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isOptionalNumber(value: unknown): boolean {
  return value == null || (typeof value === 'number' && Number.isFinite(value));
}

function isBasicDraft(value: unknown): boolean {
  return isObject(value) && typeof value.enabled === 'boolean'
    && isOptionalNumber(value.min) && isOptionalNumber(value.max)
    && (value.values === undefined || (Array.isArray(value.values)
      && value.values.every((category) => typeof category === 'string')));
}

function isSideDraft(value: unknown): boolean {
  return isObject(value) && typeof value.enabled === 'boolean'
    && isOptionalNumber(value.gpm) && isOptionalNumber(value.avg_views)
    && isOptionalNumber(value.engagement);
}

/** Only checks form shape: incomplete edits can be remembered, never authorized for execution. */
export function readRuleMemory(storage: Pick<Storage, 'getItem'>): RuleMemory | null {
  try {
    const memory: unknown = JSON.parse(storage.getItem(RULE_MEMORY_KEY) ?? 'null');
    if (!isObject(memory) || (memory.displayMode !== 'standard' && memory.displayMode !== 'custom')) {
      return null;
    }
    const rule = memory.rule;
    if (!isObject(rule) || rule.schema_version !== 1 || rule.mode !== 'custom'
      || !Array.isArray(rule.product_ids) || !rule.product_ids.every((productId) => typeof productId === 'string')
      || !isObject(rule.basic)) {
      return null;
    }
    const basic = rule.basic;
    if (!BASIC_KEYS.every((key) => isBasicDraft(basic[key]))) {
      return null;
    }
    const group = rule.video_live;
    const content = rule.content;
    if (!isObject(group) || typeof group.enabled !== 'boolean'
      || (group.logic !== 'either' && group.logic !== 'both')
      || !isSideDraft(group.video) || !isSideDraft(group.live)
      || !isObject(content) || typeof content.enabled !== 'boolean'
      || typeof content.days !== 'number' || !Number.isFinite(content.days)
      || typeof content.min_related !== 'number' || !Number.isFinite(content.min_related)
      || typeof content.require_display !== 'boolean') {
      return null;
    }
    // Server snapshots omit values on disabled metrics; the form expects arrays.
    for (const key of BASIC_KEYS) {
      const condition = basic[key] as Record<string, unknown>;
      condition.values ??= [];
    }
    return memory as unknown as RuleMemory;
  } catch {
    return null;
  }
}

export function writeRuleMemory(storage: Pick<Storage, 'setItem'>, memory: RuleMemory): boolean {
  try {
    storage.setItem(RULE_MEMORY_KEY, JSON.stringify(memory));
    return true;
  } catch {
    return false;
  }
}

export function loadBrowserRuleMemory(): RuleMemory | null {
  try {
    return readRuleMemory(window.localStorage);
  } catch {
    return null;
  }
}

export function rememberBrowserRule(memory: RuleMemory): boolean {
  try {
    return writeRuleMemory(window.localStorage, memory);
  } catch {
    return false;
  }
}
