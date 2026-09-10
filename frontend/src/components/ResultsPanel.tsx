import {Badge} from '@astryxdesign/core/Badge';
import {Banner} from '@astryxdesign/core/Banner';
import {CheckboxInput} from '@astryxdesign/core/CheckboxInput';
import {Heading} from '@astryxdesign/core/Heading';
import {Layout, LayoutContent, LayoutPanel} from '@astryxdesign/core/Layout';
import {Link} from '@astryxdesign/core/Link';
import {Section} from '@astryxdesign/core/Section';
import {Stack} from '@astryxdesign/core/Stack';
import {StatusDot} from '@astryxdesign/core/StatusDot';
import {Tab, TabList} from '@astryxdesign/core/TabList';
import {Table, proportional, pixel} from '@astryxdesign/core/Table';
import {Text} from '@astryxdesign/core/Text';
import {TextInput} from '@astryxdesign/core/TextInput';
import {useMemo, useState} from 'react';

import type {CandidateRow, PreviewPayload} from '../types';

type FilterTab = 'all' | 'eligible' | 'needs_review' | 'failed' | 'blocked';

interface ResultsPanelProps {
  preview: PreviewPayload;
  selection: string[];
  onSelectionChange: (applyIds: string[]) => void;
  previewInvalidated: boolean;
  staleReason: string;
}

const STATUS_LABELS: Record<string, string> = {
  passed: '符合',
  failed: '不符合',
  needs_review: '待复核',
  not_checked: '未检查',
  not_run: '未运行',
};

function statusVariant(status: string): 'success' | 'error' | 'warning' | 'neutral' {
  if (status === 'passed') {
    return 'success';
  }
  if (status === 'failed') {
    return 'error';
  }
  if (status === 'needs_review') {
    return 'warning';
  }
  return 'neutral';
}

function rowOverall(row: CandidateRow): string {
  if (row.blocked) {
    return 'blocked';
  }
  if (row.custom_eligible) {
    return 'eligible';
  }
  return row.overall;
}

function rowStatusLabel(row: CandidateRow): string {
  const overall = rowOverall(row);
  if (overall === 'eligible') {
    return '符合';
  }
  if (overall === 'blocked') {
    return '执行拦截';
  }
  return STATUS_LABELS[row.overall] ?? row.overall;
}

function rowStatusVariant(row: CandidateRow): 'success' | 'error' | 'warning' | 'neutral' {
  const overall = rowOverall(row);
  if (overall === 'eligible') {
    return 'success';
  }
  if (overall === 'blocked') {
    return 'error';
  }
  return statusVariant(row.overall);
}

export function ResultsPanel({
  preview,
  selection,
  onSelectionChange,
  previewInvalidated,
  staleReason,
}: ResultsPanelProps) {
  // 筛查跑完默认只看符合项；待复核/不符合/拦截仍可切页签查看。
  const [filter, setFilter] = useState<FilterTab>('eligible');
  const [search, setSearch] = useState('');
  const [selectedApplyId, setSelectedApplyId] = useState<string | null>(null);

  const rows = useMemo(() => {
    const normalizedSearch = search.trim().toLowerCase();
    return preview.rows.filter((row) => {
      const overall = rowOverall(row);
      if (filter === 'eligible' && overall !== 'eligible') {
        return false;
      }
      if (filter === 'needs_review' && overall !== 'needs_review') {
        return false;
      }
      if (filter === 'failed' && overall !== 'failed') {
        return false;
      }
      if (filter === 'blocked' && overall !== 'blocked') {
        return false;
      }
      if (
        normalizedSearch !== '' &&
        !`${row.creator_name} ${row.creator_id} ${row.apply_id} ${row.product_id}`
          .toLowerCase()
          .includes(normalizedSearch)
      ) {
        return false;
      }
      return true;
    });
  }, [preview.rows, filter, search]);

  const selectedRow = useMemo(
    () => preview.rows.find((row) => row.apply_id === selectedApplyId) ?? null,
    [preview.rows, selectedApplyId],
  );

  const stats = preview.stats;

  return (
    <Section>
      <Stack gap={3}>
        <Stack direction="horizontal" gap={2} vAlign="center">
          <Heading level={2}>3 · 只读结果</Heading>
          <Badge label={`合计 ${stats?.rows ?? 0}`} />
          <Badge label={`符合 ${stats?.eligible ?? 0}`} />
          <Badge label={`待复核 ${stats?.needs_review ?? 0}`} />
          <Badge label={`不符合 ${stats?.failed ?? 0}`} />
          <Badge label={`拦截 ${stats?.blocked ?? 0}`} />
        </Stack>

        {previewInvalidated && (
          <Banner
            status="warning"
            title="结果已失效"
            description={staleReason || '规则已修改，旧结果不可用于执行。'}
          />
        )}
        {!preview.integrity_complete && (
          <Banner
            status="warning"
            title="采集不完整"
            description="部分详情或内容证据采集失败；完整性不足的批次不能用于执行批准。"
          />
        )}

        <Stack direction="horizontal" gap={2} vAlign="center">
          <TabList value={filter} onChange={(value) => setFilter(value as FilterTab)} hasDivider>
            <Tab value="all" label="全部" />
            <Tab value="eligible" label="符合" />
            <Tab value="needs_review" label="待复核" />
            <Tab value="failed" label="不符合" />
            <Tab value="blocked" label="拦截" />
          </TabList>
          <TextInput
            label="搜索达人/申请/商品"
            value={search}
            onChange={(value) => setSearch(value)}
            placeholder="达人名 / 申请 ID / 商品 ID"
          />
        </Stack>

        <Layout
          content={
            <LayoutContent>
              <Table
                data={rows as unknown as Record<string, unknown>[]}
                idKey="apply_id"
                density="compact"
                hasHover
                columns={[
                  {
                    key: 'select',
                    header: '',
                    width: pixel(44),
                    renderCell: (row) => {
                      const candidate = row as unknown as CandidateRow;
                      const selectable =
                        candidate.custom_eligible && !candidate.blocked && !previewInvalidated && preview.integrity_complete;
                      return (
                        <CheckboxInput
                          size="sm"
                          label={`选择 ${candidate.creator_name}`}
                          isLabelHidden
                          value={
                            selection.includes(candidate.apply_id)
                              ? true
                              : selectable
                                ? false
                                : false
                          }
                          isDisabled={!selectable && !selection.includes(candidate.apply_id)}
                          onChange={(checked) => {
                            const next = checked
                              ? [...selection, candidate.apply_id]
                              : selection.filter((id) => id !== candidate.apply_id);
                            onSelectionChange(next);
                          }}
                        />
                      );
                    },
                  },
                  {
                    key: 'creator_name',
                    header: '达人',
                    width: proportional(2),
                    renderCell: (row) => {
                      const candidate = row as unknown as CandidateRow;
                      return (
                        <Link onClick={() => setSelectedApplyId(candidate.apply_id)}>
                          {candidate.creator_name}
                        </Link>
                      );
                    },
                  },
                  {
                    key: 'apply_id',
                    header: '申请 ID',
                    width: proportional(2),
                  },
                  {
                    key: 'product_id',
                    header: '商品',
                    width: proportional(2),
                  },
                  {
                    key: 'status',
                    header: '结论',
                    width: pixel(100),
                    renderCell: (row) => {
                      const candidate = row as unknown as CandidateRow;
                      return (
                        <Stack direction="horizontal" gap={1} vAlign="center">
                          <StatusDot
                            variant={rowStatusVariant(candidate)}
                            label={rowStatusLabel(candidate)}
                          />
                          <Text>{rowStatusLabel(candidate)}</Text>
                        </Stack>
                      );
                    },
                  },
                  {
                    key: 'checks',
                    header: '检查摘要',
                    width: proportional(4),
                    renderCell: (row) => {
                      const candidate = row as unknown as CandidateRow;
                      if (candidate.blocked) {
                        const reasons = candidate.safety_blocks
                          .map((block) => block.detail)
                          .join('；');
                        return (
                          <Text type="supporting">
                            执行拦截：{reasons || '不满足执行条件'}
                          </Text>
                        );
                      }
                      return (
                        <Text type="supporting">
                          {candidate.checks
                            .filter((check) => check.status !== 'not_checked')
                            .map((check) => `${check.label}:${STATUS_LABELS[check.status] ?? check.status}`)
                            .join(' · ')}
                        </Text>
                      );
                    },
                  },
                  {
                    key: 'content',
                    header: '内容',
                    width: pixel(110),
                    renderCell: (row) => {
                      const candidate = row as unknown as CandidateRow;
                      const label = candidate.content_verdict === 'not_run'
                        ? '未运行'
                        : candidate.content_verdict === 'not_checked'
                          ? '未执行'
                          : `${STATUS_LABELS[candidate.content_verdict] ?? candidate.content_verdict}（${candidate.content_related_count}条）`;
                      return (
                        <Stack direction="horizontal" gap={1} vAlign="center">
                          <StatusDot
                            variant={statusVariant(candidate.content_verdict)}
                            label={label}
                          />
                          <Text>{label}</Text>
                        </Stack>
                      );
                    },
                  },
                  {
                    key: 'observed_at',
                    header: '采集时间',
                    width: proportional(2),
                  },
                ]}
              />
            </LayoutContent>
          }
          end={
            selectedRow !== null ? (
              <LayoutPanel width={360} hasDivider>
                <Stack gap={2}>
                  <Heading level={3}>{selectedRow.creator_name}</Heading>
                  <Text>申请 ID：{selectedRow.apply_id}</Text>
                  <Text>商品：{selectedRow.product_id}</Text>
                  <Heading level={4}>逐项检查</Heading>
                  {selectedRow.checks.map((check) => (
                    <Stack key={check.key} direction="horizontal" gap={2} vAlign="center">
                      <StatusDot
                        variant={statusVariant(check.status)}
                        label={check.label}
                      />
                      <Text>
                        {check.label}：{STATUS_LABELS[check.status] ?? check.status}
                        {check.value !== null && check.value !== undefined
                          ? `（值 ${String(check.value)}）`
                          : ''}
                        {check.source !== '' ? ` · 来源 ${check.source}` : ''}
                      </Text>
                    </Stack>
                  ))}
                  {selectedRow.safety_blocks.length > 0 && (
                    <Stack gap={1}>
                      <Heading level={4}>执行拦截</Heading>
                      {selectedRow.safety_blocks.map((block) => (
                        <Text key={block.code} type="supporting">
                          {block.detail}
                        </Text>
                      ))}
                    </Stack>
                  )}
                  {selectedRow.content_reason !== '' && (
                    <Text type="supporting">内容证据：{selectedRow.content_reason}</Text>
                  )}
                </Stack>
              </LayoutPanel>
            ) : undefined
          }
        />
      </Stack>
    </Section>
  );
}
