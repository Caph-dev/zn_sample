import {Banner} from '@astryxdesign/core/Banner';
import {Button} from '@astryxdesign/core/Button';
import {EmptyState} from '@astryxdesign/core/EmptyState';
import {Link} from '@astryxdesign/core/Link';
import {Section} from '@astryxdesign/core/Section';
import {Selector} from '@astryxdesign/core/Selector';
import {Stack} from '@astryxdesign/core/Stack';
import {Tab, TabList} from '@astryxdesign/core/TabList';
import {Table, pixel, proportional} from '@astryxdesign/core/Table';
import {Text} from '@astryxdesign/core/Text';
import {useMemo, useState} from 'react';

import {PageHeader} from '../components/PageHeader';
import {OperatorGroups} from '../components/OperatorGroups';
import {StatusToken} from '../components/StatusToken';
import type {FollowupRow, FollowupsData} from '../types';

const QUICK_FILTERS = [
  {value: '', label: '全部', href: '/followups'},
  {value: 'stage:arrival', label: '今日到货', href: '/followups?stage=arrival'},
  {value: 'stage:day_3', label: 'D+3', href: '/followups?stage=day_3'},
  {value: 'stage:day_7', label: 'D+7', href: '/followups?stage=day_7'},
  {value: 'stage:day_10_list', label: 'D+10 出名单', href: '/followups?stage=day_10_list'},
  {value: 'stage:unfulfilled', label: 'D+15 未履约', href: '/followups?stage=unfulfilled'},
  {value: 'status:needs_review', label: '需确认', href: '/followups?status=needs_review'},
];

function activeQuickFilter(data: FollowupsData): string {
  if (data.filters.status !== '') {
    return `status:${data.filters.status}`;
  }
  if (data.filters.stage !== '') {
    return `stage:${data.filters.stage}`;
  }
  return '';
}

export function FollowupsPage({data}: {data: FollowupsData}) {
  const [stage, setStage] = useState(data.filters.stage);
  const [status, setStatus] = useState(data.filters.status);
  const [language, setLanguage] = useState(data.filters.language);
  const [platformStatus, setPlatformStatus] = useState(data.filters.platform_status);

  const withAllOption = (options: {value: string; label: string}[]) => [
    {value: '', label: '全部'},
    ...options,
  ];

  const rowNumbers = useMemo(
    () => new Map(data.rows.map((row, index) => [row.id, index + 1])),
    [data.rows],
  );

  return (
    <Stack gap={4}>
      <PageHeader
        eyebrow="本地预览"
        title="达人跟进"
        description="先跑物流与跟进动作生成待办，再逐条预览、人工分类和本地完成标记；写飞书必须勾选显式开关，默认不发私信。"
        actions={
          <form
            method="post"
            action="/api/jobs/report-export"
            data-job-form
            data-job-label="到货后第 10 天名单"
          >
            <input type="hidden" name="kind" value="day_10_list" />
            <Button type="submit" label="导出到货后第 10 天名单" variant="secondary" />
          </form>
        }
      />

      <Section>
        <OperatorGroups groups={data.operator_groups} />
      </Section>

      <Banner
        status="info"
        title="默认只读"
        description="被新阶段取代的旧步骤默认不出现；写飞书「已完成 / 未发布」必须勾选显式开关。"
      />

      <TabList
        value={activeQuickFilter(data)}
        onChange={(value) => {
          const target = QUICK_FILTERS.find((filter) => filter.value === value);
          if (target !== undefined) {
            window.location.assign(target.href);
          }
        }}
      >
        {QUICK_FILTERS.map((filter) => (
          <Tab key={filter.value} value={filter.value} label={filter.label} />
        ))}
      </TabList>

      <Section>
        <form method="get" action="/followups">
          <Stack direction="horizontal" gap={2} vAlign="end" wrap="wrap">
            <Selector
              label="阶段"
              htmlName="stage"
              hasClear
              width={220}
              placeholder="全部"
              options={withAllOption(data.filter_options.stages)}
              value={stage === '' ? null : stage}
              onChange={(value) => setStage(value ?? '')}
            />
            <Selector
              label="状态"
              htmlName="status"
              hasClear
              width={200}
              placeholder="全部"
              options={withAllOption(data.filter_options.statuses)}
              value={status === '' ? null : status}
              onChange={(value) => setStatus(value ?? '')}
            />
            <Selector
              label="语言"
              htmlName="language"
              hasClear
              width={180}
              placeholder="全部"
              options={withAllOption(data.filter_options.languages)}
              value={language === '' ? null : language}
              onChange={(value) => setLanguage(value ?? '')}
            />
            <Selector
              label="样品状态"
              htmlName="platform_status"
              hasClear
              width={200}
              placeholder="全部"
              options={withAllOption(data.filter_options.platform_statuses)}
              value={platformStatus === '' ? null : platformStatus}
              onChange={(value) => setPlatformStatus(value ?? '')}
            />
            <Button type="submit" label="筛选" variant="primary" />
          </Stack>
        </form>
      </Section>

      {data.rows.length === 0 ? (
        <EmptyState
          title="没有匹配的跟进待办"
          description="先运行「生成今日跟进待办（只读）」，或放宽筛选条件。"
        />
      ) : (
        <Table
          data={data.rows as unknown as Record<string, unknown>[]}
          idKey="id"
          density="compact"
          hasHover
          columns={[
            {
              key: 'row_number',
              header: '序号',
              width: pixel(64),
              renderCell: (row) => {
                const task = row as unknown as FollowupRow;
                return <Text type="supporting">{rowNumbers.get(task.id) ?? ''}</Text>;
              },
            },
            {
              key: 'creator_name',
              header: '达人',
              width: proportional(2),
              renderCell: (row) => {
                const task = row as unknown as FollowupRow;
                return <Link href={`/followups/${task.id}`}>{task.creator_name}</Link>;
              },
            },
            {
              key: 'stage',
              header: '阶段',
              width: pixel(150),
              renderCell: (row) => {
                const task = row as unknown as FollowupRow;
                return <StatusToken label={task.stage_label} tone={task.stage_tone} />;
              },
            },
            {
              key: 'action',
              header: '动作',
              width: proportional(2),
              renderCell: (row) => {
                const task = row as unknown as FollowupRow;
                return <StatusToken label={task.action_label} tone={task.action_tone} />;
              },
            },
            {
              key: 'status',
              header: '状态',
              width: pixel(130),
              renderCell: (row) => {
                const task = row as unknown as FollowupRow;
                return (
                  <StatusToken
                    label={task.status_label}
                    tone={task.status_tone}
                    tooltip={task.status_tooltip}
                  />
                );
              },
            },
            {
              key: 'language',
              header: '语言',
              width: pixel(100),
              renderCell: (row) => {
                const task = row as unknown as FollowupRow;
                return task.language_label === '' ? (
                  <Text type="supporting">-</Text>
                ) : (
                  <Text>{task.language_label}</Text>
                );
              },
            },
            {
              key: 'platform_status',
              header: '样品状态',
              width: pixel(120),
              renderCell: (row) => {
                const task = row as unknown as FollowupRow;
                return <Text>{task.platform_status_label}</Text>;
              },
            },
          ]}
        />
      )}
    </Stack>
  );
}
