import {Banner} from '@astryxdesign/core/Banner';
import {Button} from '@astryxdesign/core/Button';
import {CheckboxInput} from '@astryxdesign/core/CheckboxInput';
import {Divider} from '@astryxdesign/core/Divider';
import {Grid, GridSpan} from '@astryxdesign/core/Grid';
import {Heading} from '@astryxdesign/core/Heading';
import {MetadataList, MetadataListItem} from '@astryxdesign/core/MetadataList';
import {NumberInput} from '@astryxdesign/core/NumberInput';
import {Section} from '@astryxdesign/core/Section';
import {SegmentedControl, SegmentedControlItem} from '@astryxdesign/core/SegmentedControl';
import {Stack, StackItem} from '@astryxdesign/core/Stack';
import {Switch} from '@astryxdesign/core/Switch';
import {Text} from '@astryxdesign/core/Text';
import {Fragment} from 'react';

import type {BasicKey, OptionsPayload, RuleDraft, SideDraft} from '../types';
import {
  BASIC_KEYS,
  BASIC_LABELS,
  BASIC_UNITS,
  summaryRows,
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

/** 标准 SOP 只读展示行（与后端 filters.Criteria / 内容审查一致）。 */
const STANDARD_BASIC_ROWS: {label: string; value: string}[] = [
  {label: '粉丝数', value: '> 2000'},
  {label: 'GMV', value: '> 1500 USD'},
  {label: '成交件数', value: '> 80'},
  {label: '千次曝光成交/GPM', value: '> 10'},
  {label: '客单价', value: '10–25 USD'},
  {label: '履约率/预计发布率', value: '> 80%'},
  {label: '女性粉丝占比', value: '> 60%'},
  {label: '类目', value: '命中七类白名单任一项'},
];

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
  const summary = summaryRows(rule);
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
          <Heading level={2}>2 · 规则配置</Heading>
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
          <Stack gap={3}>
            <Text type="supporting">
              标准模式只读展示正式 SOP 阈值，不会修改正式默认值。要按本次临时标准审核，
              请点下方「复制为自定义」创建独立草稿。
            </Text>
            <MetadataList
              columns={2}
              label={{position: 'top'}}
              title="基础条件（全部 AND）"
            >
              {STANDARD_BASIC_ROWS.map((row) => (
                <MetadataListItem key={row.label} label={row.label}>
                  {row.value}
                </MetadataListItem>
              ))}
            </MetadataList>
            <MetadataList columns={2} label={{position: 'top'}}>
              <MetadataListItem label="视频/直播">
                任一侧达标（视频 GPM&gt;10 且均播&gt;300 且互动&gt;2%；直播 GPM&gt;12
                且均播&gt;1000）
              </MetadataListItem>
              <MetadataListItem label="内容审核">
                近 7 天 ≥4 条相关带货视频，且至少 1 条明确展示
              </MetadataListItem>
            </MetadataList>
            <Text type="supporting">
              正式跑标准 SOP 请继续使用工作台 1/2 号入口；本页的自定义规则只绑定本次任务。
            </Text>
            <Stack direction="horizontal">
              <Button label="复制为自定义" variant="primary" onClick={onCopyToCustom} />
            </Stack>
          </Stack>
        )}

        {displayMode === 'custom' && (
          <Stack gap={4}>
            <Stack gap={2}>
              <Heading level={3}>主推商品（至少一款，默认 B005）</Heading>
              {heroProducts.length === 0 && (
                <Banner
                  status="error"
                  title="主推款表不可用"
                  description="无法读取飞书主推款表；请先检查配置或点击「重新检测」。"
                />
              )}
              {heroProducts.length > 0 && (
                <Grid columns={{minWidth: 300, max: 2}} gap={2}>
                  {heroProducts.map((product) => {
                    const productId = product.product_id;
                    const isChecked = rule.product_ids.includes(productId);
                    return (
                      <CheckboxInput
                        key={productId}
                        size="sm"
                        label={`${product.sku}（${productId}）`}
                        value={isChecked}
                        onChange={(checked) =>
                          onRuleChange({
                            ...rule,
                            product_ids: checked
                              ? [...rule.product_ids, productId]
                              : rule.product_ids.filter((id) => id !== productId),
                          })
                        }
                      />
                    );
                  })}
                </Grid>
              )}
            </Stack>

            <Stack gap={2}>
              <Heading level={3}>基础条件</Heading>
              <Grid columns={{minWidth: 320, max: 2}} gap={3}>
                {BASIC_KEYS.filter((key) => key !== 'categories' && key !== 'aov').map((key) => {
                  const draft = rule.basic[key];
                  const limits = options?.basic[key];
                  const unit = BASIC_UNITS[key] ?? '';
                  return (
                    <Stack
                      key={key}
                      direction="horizontal"
                      gap={2}
                      vAlign="center"
                      hAlign="between"
                    >
                      <CheckboxInput
                        size="sm"
                        label={BASIC_LABELS[key]}
                        value={draft.enabled}
                        onChange={(checked) => updateBasic(key, {enabled: checked})}
                      />
                      <Stack direction="horizontal" gap={1} vAlign="center">
                        <NumberInput
                          label={`${BASIC_LABELS[key]}阈值`}
                          isLabelHidden
                          value={draft.min ?? null}
                          onChange={(next) => updateBasic(key, {min: next})}
                          min={limits?.min}
                          max={limits?.max}
                          size="sm"
                          width={104}
                          isDisabled={!draft.enabled}
                        />
                        {unit !== '' && <Text type="supporting">{unit}</Text>}
                      </Stack>
                    </Stack>
                  );
                })}
                <GridSpan columns="full">
                  <Stack direction="horizontal" gap={2} vAlign="center">
                    <CheckboxInput
                      size="sm"
                      label="客单价"
                      value={rule.basic.aov.enabled}
                      onChange={(checked) => updateBasic('aov', {enabled: checked})}
                    />
                    <NumberInput
                      label="客单价下限"
                      isLabelHidden
                      value={rule.basic.aov.min ?? null}
                      onChange={(next) => updateBasic('aov', {min: next})}
                      min={options?.basic.aov?.min}
                      max={options?.basic.aov?.max}
                      size="sm"
                      width={104}
                      isDisabled={!rule.basic.aov.enabled}
                    />
                    <Text type="supporting">–</Text>
                    <NumberInput
                      label="客单价上限"
                      isLabelHidden
                      value={rule.basic.aov.max ?? null}
                      onChange={(next) => updateBasic('aov', {max: next})}
                      min={options?.basic.aov?.min}
                      max={options?.basic.aov?.max}
                      size="sm"
                      width={104}
                      isDisabled={!rule.basic.aov.enabled}
                    />
                    <Text type="supporting">USD</Text>
                  </Stack>
                </GridSpan>
              </Grid>
            </Stack>

            <Stack gap={2}>
              <Heading level={3}>视频/直播数据</Heading>
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
                <Stack direction="horizontal" gap={3} vAlign="stretch">
                  {(
                    [
                      ['video', '视频'],
                      ['live', '直播'],
                    ] as const
                  ).map(([sideKey, sideLabel], index) => {
                    const side = rule.video_live[sideKey];
                    return (
                      <Fragment key={sideKey}>
                        {index > 0 && (
                          <StackItem size="static" crossAlignSelf="stretch">
                            <Divider orientation="vertical" />
                          </StackItem>
                        )}
                        <StackItem size="fill">
                          <Stack gap={2}>
                            <CheckboxInput
                              size="sm"
                              label={`${sideLabel}侧`}
                              value={side.enabled}
                              onChange={(checked) => updateSide(sideKey, {enabled: checked})}
                            />
                            {side.enabled && (
                              <Stack gap={2}>
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
                        </StackItem>
                      </Fragment>
                    );
                  })}
                </Stack>
              )}
            </Stack>

            <Stack gap={2}>
              <Heading level={3}>内容审核</Heading>
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
              <Stack gap={2}>
                <Heading level={3}>规则摘要（实时）</Heading>
                <MetadataList label={{position: 'start', width: 88}}>
                  {summary.map((row) => (
                    <MetadataListItem key={row.label} label={row.label}>
                      {row.value}
                    </MetadataListItem>
                  ))}
                </MetadataList>
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
            <Text type="supporting">
              只读筛查：扫描待审核列表并按本次规则评估，不点「同意」、不写飞书。
            </Text>
          </Stack>
        )}
      </Stack>
    </Section>
  );
}
