/** 规则草稿默认值、前端校验与实时摘要（与后端 lib.auto_approval_rules 对齐）。 */

import type {BasicKey, OptionsPayload, PreviewPayload, RuleDraft} from './types';

export const BASIC_KEYS: BasicKey[] = [
  'followers',
  'gmv',
  'units',
  'gpm',
  'aov',
  'fulfillment',
  'female_pct',
  'categories',
];

export const BASIC_LABELS: Record<BasicKey, string> = {
  followers: '粉丝数',
  gmv: 'GMV',
  units: '成交件数',
  gpm: '千次曝光成交/GPM',
  aov: '客单价',
  fulfillment: '履约率/预计发布率',
  female_pct: '女性粉丝占比',
  categories: '类目',
};

export const BASIC_UNITS: Partial<Record<BasicKey, string>> = {
  followers: '人',
  gmv: 'USD',
  units: '件',
  gpm: 'USD',
  aov: 'USD',
  fulfillment: '%',
  female_pct: '%',
};

export const DEFAULT_ACTIVE_PRODUCT_ID = '1732414717062320994';
export const B005_SKU_RULE_DESCRIPTION =
  'B005（1732414717062320994）的 SKU 不得包含 6PCS（不区分大小写）。SKU 缺失时待复核、不可批准；其他商品不受此限制。此限制固定启用。';

export function hasOutdatedSkuEvidence(preview: PreviewPayload): boolean {
  return preview.rows.some((row) => {
    if (row.product_id !== DEFAULT_ACTIVE_PRODUCT_ID) {
      return false;
    }
    const checks = row.checks.filter((check) => check.key === 'b005_sku');
    if (checks.length !== 1 || checks[0].source !== 'application.sku_desc') {
      return true;
    }
    if (!row.custom_eligible) {
      return false;
    }
    const description = row.metrics.sku_desc;
    return typeof description !== 'string' || description.trim() === ''
      || description.toUpperCase().includes('6PCS')
      || checks[0].status !== 'passed' || checks[0].value !== description.trim();
  });
}

export const DEFAULT_CATEGORIES = [
  'Beauty & Personal Care',
  'Womenswear & Underwear',
  'Household Appliances',
  'Fashion Accessories',
  'Shoes',
  'Sports & Outdoor',
  'Home Textiles',
];

/** 正式 SOP 阈值（与后端 BASIC_LIMITS.default 一致）：首次进入和复制为自定义都用它。 */
export const SOP_DEFAULTS: Record<BasicKey, {min: number; max?: number}> = {
  followers: {min: 2000},
  gmv: {min: 1500},
  units: {min: 80},
  gpm: {min: 10},
  aov: {min: 10, max: 25},
  fulfillment: {min: 80},
  female_pct: {min: 60},
  categories: {min: 0},
};

export function buildStandardRule(options: OptionsPayload | null): RuleDraft {
  const basic = {} as RuleDraft['basic'];
  for (const key of BASIC_KEYS) {
    if (key === 'aov') {
      basic[key] = {
        enabled: true,
        min: SOP_DEFAULTS.aov.min,
        max: SOP_DEFAULTS.aov.max ?? 25,
        values: [],
      };
    } else if (key === 'categories') {
      basic[key] = {enabled: true, values: sortCategories(DEFAULT_CATEGORIES)};
    } else {
      // options 由后端下发（BASIC_LIMITS.default），缺失时回落到 SOP 常量。
      const defaults = options?.basic[key];
      basic[key] = {
        enabled: true,
        min: defaults?.default ?? SOP_DEFAULTS[key].min,
        values: [],
      };
    }
  }
  return {
    schema_version: 1,
    mode: 'custom',
    product_ids: [DEFAULT_ACTIVE_PRODUCT_ID],
    basic,
    video_live: {
      enabled: true,
      logic: 'either',
      video: {enabled: true, gpm: 10, avg_views: 300, engagement: 2},
      live: {enabled: true, gpm: 12, avg_views: 1000},
    },
    content: {enabled: true, days: 7, min_related: 4, require_display: true},
  };
}

/**
 * UI 固定项归一化：
 * - 视频/直播组逻辑固定「任一侧达标」（选择框已移除）；旧草稿存 both 会被判为已修改，需重新筛查。
 * - 类目固定启用、固定使用全部白名单类目（选择框已移除），按字典序排序以对齐后端快照的 `sorted()`。
 */
export function normalizeRule(
  rule: RuleDraft,
  categories: string[] = DEFAULT_CATEGORIES,
): RuleDraft {
  const allCategories = sortCategories(
    categories.length > 0 ? categories : DEFAULT_CATEGORIES,
  );
  const next: RuleDraft = {
    ...rule,
    basic: {
      ...rule.basic,
      categories: {
        ...rule.basic.categories,
        enabled: true,
        values: allCategories,
      },
    },
  };
  if (next.video_live.logic === 'either') {
    return next;
  }
  return {...next, video_live: {...next.video_live, logic: 'either'}};
}

function sortCategories(categories: string[]): string[] {
  return [...categories].sort();
}

function sameNumber(left: number | null | undefined, right: number | null | undefined): boolean {
  if (left === null || left === undefined || right === null || right === undefined) {
    return (left ?? null) === (right ?? null);
  }
  return Number(left) === Number(right);
}

/**
 * 语义比较「当前表单规则」与「预览快照规则」。
 *
 * 不能用 JSON.stringify 直接比较：预览快照经后端 `to_dict()` 规整，类目是字典序、
 * 部分未启用字段会被省略，键序也可能不同；逐字符比较会把它们误判成「已修改」。
 * 这里只比较真正影响后端 rule_hash 的字段（启用状态与启用项的取值）。
 */
export function rulesEqual(left: RuleDraft, right: RuleDraft): boolean {
  if (left.schema_version !== right.schema_version || left.mode !== right.mode) {
    return false;
  }
  if (
    left.product_ids.length !== right.product_ids.length ||
    left.product_ids.some((productId, index) => productId !== right.product_ids[index])
  ) {
    return false;
  }
  for (const key of BASIC_KEYS) {
    const leftCondition = left.basic[key];
    const rightCondition = right.basic[key];
    if (leftCondition === undefined || rightCondition === undefined) {
      return false;
    }
    if (leftCondition.enabled !== rightCondition.enabled) {
      return false;
    }
    if (!leftCondition.enabled) {
      continue;
    }
    if (key === 'categories') {
      const leftCategories = sortCategories(leftCondition.values);
      const rightCategories = sortCategories(rightCondition.values);
      if (
        leftCategories.length !== rightCategories.length ||
        leftCategories.some((value, index) => value !== rightCategories[index])
      ) {
        return false;
      }
      continue;
    }
    if (!sameNumber(leftCondition.min, rightCondition.min)) {
      return false;
    }
    if (key === 'aov' && !sameNumber(leftCondition.max, rightCondition.max)) {
      return false;
    }
  }
  const leftGroup = left.video_live;
  const rightGroup = right.video_live;
  if (leftGroup.enabled !== rightGroup.enabled) {
    return false;
  }
  if (leftGroup.enabled) {
    if (leftGroup.logic !== rightGroup.logic) {
      return false;
    }
    for (const side of ['video', 'live'] as const) {
      const leftSide = leftGroup[side];
      const rightSide = rightGroup[side];
      if (leftSide.enabled !== rightSide.enabled) {
        return false;
      }
      if (!leftSide.enabled) {
        continue;
      }
      if (!sameNumber(leftSide.gpm, rightSide.gpm)) {
        return false;
      }
      if (!sameNumber(leftSide.avg_views, rightSide.avg_views)) {
        return false;
      }
      const engagementCompared =
        side === 'video' ||
        leftSide.engagement !== undefined ||
        rightSide.engagement !== undefined;
      if (engagementCompared && !sameNumber(leftSide.engagement, rightSide.engagement)) {
        return false;
      }
    }
  }
  const leftContent = left.content;
  const rightContent = right.content;
  if (leftContent.enabled !== rightContent.enabled) {
    return false;
  }
  if (leftContent.enabled) {
    if (
      leftContent.days !== rightContent.days ||
      leftContent.min_related !== rightContent.min_related ||
      leftContent.require_display !== rightContent.require_display
    ) {
      return false;
    }
  }
  return true;
}

/** 前端校验（服务端仍会再次严格校验）。返回错误消息数组，空数组=通过。 */
export function validateDraft(rule: RuleDraft, options: OptionsPayload | null): string[] {
  const errors: string[] = [];
  if (rule.product_ids.length === 0) {
    errors.push('至少选择一款主推商品。');
  }
  const limits = options?.basic ?? {};
  const isFiniteNumber = (value: unknown): value is number =>
    typeof value === 'number' && Number.isFinite(value);
  for (const key of BASIC_KEYS) {
    const draft = rule.basic[key];
    if (!draft.enabled) {
      continue;
    }
    if (key === 'categories') {
      if (draft.values.length === 0) {
        errors.push('类目启用时必须选择至少一个类目。');
      }
      continue;
    }
    if (key === 'aov') {
      if (!isFiniteNumber(draft.min) || !isFiniteNumber(draft.max)) {
        errors.push('客单价启用时必须填写上下限。');
      } else if (draft.min > draft.max) {
        errors.push('客单价下限必须 ≤ 上限。');
      }
      continue;
    }
    if (!isFiniteNumber(draft.min)) {
      errors.push(`${BASIC_LABELS[key]}启用时必须填写阈值。`);
      continue;
    }
    const range = limits[key];
    if (range && (draft.min < range.min || draft.min > range.max)) {
      errors.push(`${BASIC_LABELS[key]}阈值超出范围 [${range.min}, ${range.max}]。`);
    }
    if (range?.integer && !Number.isInteger(draft.min)) {
      errors.push(`${BASIC_LABELS[key]}阈值必须是整数。`);
    }
  }
  const group = rule.video_live;
  if (group.enabled) {
    const sideErrors: string[] = [];
    const checkSide = (
      sideLabel: string,
      side: {enabled: boolean; gpm?: number | null; avg_views?: number | null},
    ) => {
      if (!side.enabled) {
        return;
      }
      if (!isFiniteNumber(side.gpm)) {
        sideErrors.push(`${sideLabel} GPM 启用时必须填写阈值。`);
      }
      if (!isFiniteNumber(side.avg_views)) {
        sideErrors.push(`${sideLabel} 均播启用时必须填写阈值。`);
      }
    };
    checkSide('视频', group.video);
    checkSide('直播', group.live);
    const enabledSides = [group.video.enabled, group.live.enabled].filter(Boolean).length;
    if (enabledSides === 0) {
      errors.push('视频/直播组启用时至少启用一侧。');
    }
    if (group.logic === 'both' && enabledSides < 2) {
      errors.push('「两侧均满足」必须同时启用视频与直播。');
    }
    errors.push(...sideErrors);
  }
  const anyBasicEnabled = BASIC_KEYS.some((key) => rule.basic[key].enabled);
  if (!anyBasicEnabled && !group.enabled && !rule.content.enabled) {
    errors.push('不允许只选商品而关闭全部审核项；请至少启用一项检查。');
  }
  return errors;
}

export interface SummaryRow {
  label: string;
  value: string;
}

/** 规则摘要的结构化行，供页面用 MetadataList 排版（避免整段文字堆叠）。 */
export function summaryRows(rule: RuleDraft): SummaryRow[] {
  const rows: SummaryRow[] = [];
  rows.push({
    label: '模式',
    value: '自定义（本次临时标准，不代表完整 SOP 通过）',
  });
  rows.push({label: '商品', value: rule.product_ids.join('、')});
  if (rule.product_ids.includes(DEFAULT_ACTIVE_PRODUCT_ID)) {
    rows.push({label: '商品固定限制', value: B005_SKU_RULE_DESCRIPTION});
  }
  const enabledParts: string[] = [];
  const disabledParts: string[] = [];
  for (const key of BASIC_KEYS) {
    const draft = rule.basic[key];
    if (!draft.enabled) {
      disabledParts.push(BASIC_LABELS[key]);
      continue;
    }
    if (key === 'aov') {
      enabledParts.push(`${BASIC_LABELS[key]} ${draft.min}–${draft.max} USD`);
    } else if (key === 'categories') {
      enabledParts.push(`${BASIC_LABELS[key]}命中：${draft.values.join('、')}`);
    } else {
      const unit = BASIC_UNITS[key] ?? '';
      enabledParts.push(`${BASIC_LABELS[key]} > ${draft.min}${unit}`);
    }
  }
  rows.push({
    label: '基础条件',
    value: enabledParts.length > 0 ? enabledParts.join('；') : '无（全部未检查）',
  });
  if (disabledParts.length > 0) {
    rows.push({label: '未检查', value: disabledParts.join('、')});
  }
  const group = rule.video_live;
  if (group.enabled) {
    const sideText = (
      label: string,
      side: {
        enabled: boolean;
        gpm?: number | null;
        avg_views?: number | null;
        engagement?: number | null;
      },
    ) => {
      if (!side.enabled) {
        return `${label}未启用`;
      }
      const parts = [`GPM>${side.gpm}`];
      if (side.avg_views != null) {
        parts.push(`均播>${side.avg_views}`);
      }
      if (side.engagement != null) {
        parts.push(`互动>${side.engagement}%`);
      }
      return `${label}（${parts.join(' 且 ')}）`;
    };
    rows.push({
      label: '视频/直播',
      value: `${group.logic === 'either' ? '任一侧达标' : '两侧均满足'}：${sideText('视频', group.video)}；${sideText('直播', group.live)}`,
    });
  } else {
    rows.push({label: '视频/直播', value: '未检查（not_checked）'});
  }
  if (rule.content.enabled) {
    rows.push({
      label: '内容审核',
      value: `最近 ${rule.content.days} 天 ≥ ${rule.content.min_related} 条相关带货视频${
        rule.content.require_display ? '，至少 1 条明确展示' : '（不要求展示证据）'
      }`,
    });
  } else {
    rows.push({
      label: '内容审核',
      value: '未执行（风险：不核对近期带货内容）',
    });
  }
  rows.push({
    label: '指标口径',
    value: '履约（预计发布率）、GPM、客单价均详情值优先；缺失时回退列表履约率、列表近似 GPM、GMV÷件数',
  });
  return rows;
}

/** 纯文本摘要（命令行/日志等场景）。 */
export function summaryLines(rule: RuleDraft): string[] {
  return summaryRows(rule).map((row) => `${row.label}：${row.value}`);
}
