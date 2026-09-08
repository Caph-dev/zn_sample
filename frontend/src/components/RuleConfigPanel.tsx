import {Banner} from '@astryxdesign/core/Banner';
import {Button} from '@astryxdesign/core/Button';
import {CheckboxInput} from '@astryxdesign/core/CheckboxInput';
import {CheckboxList, CheckboxListItem} from '@astryxdesign/core/CheckboxList';
import {Grid} from '@astryxdesign/core/Grid';
import {NumberInput} from '@astryxdesign/core/NumberInput';
import {Section} from '@astryxdesign/core/Section';
import {SegmentedControl, SegmentedControlItem} from '@astryxdesign/core/SegmentedControl';
import {Stack} from '@astryxdesign/core/Stack';
import {Switch} from '@astryxdesign/core/Switch';

import type {BasicKey, OptionsPayload, RuleDraft, SideDraft} from '../types';
import {
  BASIC_KEYS,
  BASIC_LABELS,
  BASIC_UNITS,
  summaryLines,
  validateDraft,
} from '../ruleModel';

interface RuleConfigPanelProps {
  options: OptionsPayload | null;
  rule: RuleDraft;
  displayMode: 'standard' | 'custom';
  onDisplayModeChange: (mode: 'standard' | 'custom') => void;
  onCopyToCustom: () => void;
  onRuleChange: (rule: RuleDraft) => void;
  onStartPreview: () => void;
  previewRunning: boolean;
  busy: boolean;
  previewInvalidated: boolean;
}

export function RuleConfigPanel({
  options,
  rule,
  displayMode,
  onDisplayModeChange,
  onCopyToCustom,
  onRuleChange,
  onStartPreview,
  previewRunning,
  busy,
  previewInvalidated,
}: RuleConfigPanelProps) {
  const errors = validateDraft(rule, options);
  const summary = summaryLines(rule);
  const heroProducts = options?.hero.products ?? [];

  const updateBasic = (key: BasicKey, patch: Partial<RuleDraft['basic'][BasicKey]>) => {
    onRuleChange({
      ...rule,
      basic: {...rule.basic, [key]: {...rule.basic[key], ...patch}},
    });
  };

  const updateSide = (side: 'video' | 'live', patch: Partial<SideDraft>) => {
    onRuleChange({
      ...rule,
      video_live: {...rule.video_live, [side]: {...rule.video_live[side], ...patch}},
    });
  };

  const numberInput = (
    key: string,
    label: string,
    value: number | null | undefined,
    onChange: (next: number) => void,
    extra: {
      min?: number;
      max?: number;
      disabled?: boolean;
      description?: string;
    } = {},
  ) => (
    <NumberInput
      key={key}
      label={label}
      value={value ?? null}
      onChange={onChange}
      min={extra.min ?? 0}
      max={extra.max}
      isDisabled={extra.disabled}
      description={extra.description}
      size="sm"
    />
  );

  return (
    <Section>
      <Stack gap={3}>
        <Stack direction="horizontal" gap={2} vAlign="center" justify="between">
          <h2 className="section-title">2 · 规则配置</h2>
          <SegmentedControl
            value={displayMode}
            onChange={(value) => onDisplayModeChange(value as 'standard' | 'custom')}
            label="审核模式"
          >
            <SegmentedControlItem value="standard" label="标准 SOP（只读）" />
            <SegmentedControlItem value="custom" label="自定义" />
          </SegmentedControl>
        </Stack>

        {displayMode === 'standard' && (
          <Stack gap={2}>
            <p>
              正式标准 SOP：粉丝 &gt;2000 · GMV &gt;1500 · 成交 &gt;80 · GPM &gt;10 ·
              客单价 10–25 USD · 履约 &gt;80% · 女性 &gt;60% · 七类目 ·
              视频/直播任一侧达标 · 近 7 天 ≥4 条相关视频且有展示证据。
            </p>
            <p>
              标准模式只读展示；要按本次临时标准审核，请「复制为自定义」创建独立草稿，
              不会修改正式默认值。正式跑标准 SOP 请继续使用工作台 1/2 号入口。
            </p>
            <Button label="复制为自定义" variant="primary" onClick={onCopyToCustom} />
          </Stack>
        )}

        {displayMode === 'custom' && (
          <Stack gap={4}>
            <Stack gap={2}>
              <h3 className="subsection-title">主推商品（至少一款，默认 B005）</h3>
              {heroProducts.length === 0 && (
                <Banner
                  status="error"
                  title="主推款表不可用"
                  description="无法读取飞书主推款表；请先检查配置或点击「重新检测」。"
                />
              )}
              {heroProducts.length > 0 && (
                <CheckboxList
                  label="主推商品"
                  value={rule.product_ids}
                  onChange={(values) => onRuleChange({...rule, product_ids: values})}
                >
                  {heroProducts.map((product) => (
                    <CheckboxListItem
                      key={product.product_id}
                      value={product.product_id}
                      label={`${product.sku}（${product.product_id}）`}
                    />
                  ))}
                </CheckboxList>
              )}
            </Stack>

            <Stack gap={2}>
              <h3 className="subsection-title">基础条件（之间为 AND；不启用 = not_checked）</h3>
              <Grid columns={{minWidth: 320, max: 2}} gap={3}>
                {BASIC_KEYS.filter((key) => key !== 'categories').map((key) => {
                  const draft = rule.basic[key];
                  const limits = options?.basic[key];
                  const unit = BASIC_UNITS[key] ?? '';
                  return (
                    <Stack key={key} gap={1}>
                      <CheckboxInput
                        label={BASIC_LABELS[key]}
                        value={draft.enabled}
                        onChange={(checked) => updateBasic(key, {enabled: checked})}
                      />
                      {key === 'aov' ? (
                        <Stack direction="horizontal" gap={2}>
                          {numberInput(
                            `${key}-min`,
                            `下限${unit ? `（${unit}）` : ''}`,
                            draft.min,
                            (next) => updateBasic(key, {min: next}),
                            {min: limits?.min, max: limits?.max, disabled: !draft.enabled},
                          )}
                          {numberInput(
                            `${key}-max`,
                            `上限${unit ? `（${unit}）` : ''}`,
                            draft.max,
                            (next) => updateBasic(key, {max: next}),
                            {min: limits?.min, max: limits?.max, disabled: !draft.enabled},
                          )}
                        </Stack>
                      ) : (
                        <Stack direction="horizontal" gap={2}>
                          {numberInput(
                            `${key}-min`,
                            `阈值${unit ? `（${unit}）` : ''}`,
                            draft.min,
                            (next) => updateBasic(key, {min: next}),
                            {min: limits?.min, max: limits?.max, disabled: !draft.enabled},
                          )}
                        </Stack>
                      )}
                    </Stack>
                  );
                })}
              </Grid>
              <Stack gap={1}>
                <CheckboxInput
                  label="类目（命中任一项）"
                  value={rule.basic.categories.enabled}
                  onChange={(checked) => updateBasic('categories', {enabled: checked})}
                />
                {rule.basic.categories.enabled && (
                  <CheckboxList
                    label="允许类目"
                    value={rule.basic.categories.values}
                    onChange={(values) => updateBasic('categories', {values})}
                  >
                    {(options?.categories ?? []).map((category) => (
                      <CheckboxListItem key={category} value={category} label={category} />
                    ))}
                  </CheckboxList>
                )}
              </Stack>
            </Stack>

            <Stack gap={2}>
              <h3 className="subsection-title">视频/直播（高级组；启用时至少一侧）</h3>
              <Switch
                label="启用视频/直播组"
                value={rule.video_live.enabled}
                onChange={(checked) =>
                  onRuleChange({
                    ...rule,
                    video_live: {...rule.video_live, enabled: checked},
                  })
                }
              />
              {rule.video_live.enabled && (
                <Stack gap={2}>
                  <SegmentedControl
                    label="组逻辑"
                    value={rule.video_live.logic}
                    onChange={(value) =>
                      onRuleChange({
                        ...rule,
                        video_live: {...rule.video_live, logic: value as 'either' | 'both'},
                      })
                    }
                  >
                    <SegmentedControlItem value="either" label="任一侧达标" />
                    <SegmentedControlItem value="both" label="两侧均满足" />
                  </SegmentedControl>
                  {(
                    [
                      ['video', '视频'],
                      ['live', '直播'],
                    ] as const
                  ).map(([sideKey, sideLabel]) => {
                    const side = rule.video_live[sideKey];
                    return (
                      <Stack key={sideKey} gap={1}>
                        <CheckboxInput
                          label={`${sideLabel}侧`}
                          value={side.enabled}
                          onChange={(checked) => updateSide(sideKey, {enabled: checked})}
                        />
                        {side.enabled && (
                          <Stack direction="horizontal" gap={2}>
                            {numberInput(
                              `${sideKey}-gpm`,
                              `${sideLabel} GPM`,
                              side.gpm,
                              (next) => updateSide(sideKey, {gpm: next}),
                              {disabled: !side.enabled},
                            )}
                            {numberInput(
                              `${sideKey}-views`,
                              `${sideLabel} 均播`,
                              side.avg_views,
                              (next) => updateSide(sideKey, {avg_views: next}),
                              {disabled: !side.enabled},
                            )}
                            {sideKey === 'video' &&
                              numberInput(
                                'video-engagement',
                                '视频互动率（%）',
                                side.engagement,
                                (next) => updateSide('video', {engagement: next}),
                                {disabled: !side.enabled, max: 100},
                              )}
                          </Stack>
                        )}
                      </Stack>
                    );
                  })}
                </Stack>
              )}
            </Stack>

            <Stack gap={2}>
              <h3 className="subsection-title">内容审核</h3>
              <Switch
                label="启用内容审核（近 7 天带货视频）"
                value={rule.content.enabled}
                onChange={(checked) =>
                  onRuleChange({...rule, content: {...rule.content, enabled: checked}})
                }
              />
              {rule.content.enabled && (
                <Stack direction="horizontal" gap={2}>
                  {numberInput(
                    'content-days',
                    '最近天数',
                    rule.content.days,
                    (next) => onRuleChange({...rule, content: {...rule.content, days: next}}),
                    {
                      min: 7,
                      max: 7,
                      disabled: true,
                      description: '当前版本与内容审核库一致，仅支持 7 天',
                    },
                  )}
                  {numberInput(
                    'content-min-related',
                    '相关视频数（至少）',
                    rule.content.min_related,
                    (next) =>
                      onRuleChange({...rule, content: {...rule.content, min_related: next}}),
                    {
                      min: 4,
                      max: 4,
                      disabled: true,
                      description: '当前版本与内容审核库一致，仅支持至少 4 条',
                    },
                  )}
                </Stack>
              )}
              {rule.content.enabled && (
                <Switch
                  label="要求至少 1 条明确展示（穿在身上或同画面露脸手持）"
                  value={rule.content.require_display}
                  onChange={(checked) =>
                    onRuleChange({
                      ...rule,
                      content: {...rule.content, require_display: checked},
                    })
                  }
                />
              )}
              {!rule.content.enabled && (
                <Banner
                  status="warning"
                  title="内容审核已关闭"
                  description="本次筛查不核对近期带货内容；证据将标记为「未执行（not_checked）」，不代表内容审核通过。"
                />
              )}
            </Stack>

            <Section variant="muted" padding={3}>
              <Stack gap={1}>
                <h3 className="subsection-title">规则摘要（实时）</h3>
                {summary.map((line) => (
                  <p key={line} className="summary-line">
                    {line}
                  </p>
                ))}
              </Stack>
            </Section>

            {errors.length > 0 && (
              <Banner
                status="error"
                title="规则未通过校验"
                description={errors.join('；')}
              />
            )}
            {previewInvalidated && (
              <Banner
                status="warning"
                title="规则已修改"
                description="规则或阈值发生变化，旧筛查结果已失效；请重新「开始只读筛查」。"
              />
            )}
            <Button
              label="开始只读筛查"
              variant="primary"
              size="lg"
              width="100%"
              isDisabled={errors.length > 0 || busy || previewRunning}
              isLoading={previewRunning}
              onClick={onStartPreview}
            />
            <p className="hint-text">
              只读筛查：扫描待审核列表并按本次规则评估，不点「同意」、不写飞书。
            </p>
          </Stack>
        )}
      </Stack>
    </Section>
  );
}
