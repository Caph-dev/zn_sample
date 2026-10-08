import {Banner} from '@astryxdesign/core/Banner';
import {Button} from '@astryxdesign/core/Button';
import {CheckboxInput} from '@astryxdesign/core/CheckboxInput';
import {EmptyState} from '@astryxdesign/core/EmptyState';
import {Heading} from '@astryxdesign/core/Heading';
import {Link} from '@astryxdesign/core/Link';
import {NumberInput} from '@astryxdesign/core/NumberInput';
import {Section} from '@astryxdesign/core/Section';
import {Selector} from '@astryxdesign/core/Selector';
import {Stack} from '@astryxdesign/core/Stack';
import {Tab, TabList} from '@astryxdesign/core/TabList';
import {Table, pixel, proportional} from '@astryxdesign/core/Table';
import {Text} from '@astryxdesign/core/Text';
import {useEffect, useMemo, useState} from 'react';

import {PageHeader} from '../components/PageHeader';
import {OperatorGroups} from '../components/OperatorGroups';
import {StatusToken} from '../components/StatusToken';
import type {FollowupRow, FollowupsData, UnfulfilledCandidate, UnfulfilledData, UnfulfilledJobScope} from '../types';

const UNFULFILLED_JOB_ACTION = '/api/jobs/followups/unfulfilled';
const ACTIVE_JOB_STATUSES = new Set(['pending', 'running']);

export function selectUnfulfilledRows(
  candidates: UnfulfilledCandidate[], storeId: string, selectedIds: number[],
) {
  const selected = new Set(selectedIds);
  return candidates.filter((candidate) => storeId !== ''
    && candidate.store_id === storeId && selected.has(candidate.task_id));
}

export function unfulfilledExecutionCount(selectedCount: number, executeLimit: number) {
  return Number.isSafeInteger(executeLimit) && executeLimit > 0
    ? Math.min(selectedCount, executeLimit) : 0;
}

export function unfulfilledConfirmationDescription(storeName: string, storeId: string, count: number) {
  return `本地店铺 ${storeName}（${storeId}）；本次实际最多处理 ${count} 条，按排期和任务 ID 排序。`
    + '只写飞书合作状态「待发布 → 未发布」，不发私信，不改其它列。'
    + '请先同步物流并生成今日待办；本地名单不代表刚刚实店核验。';
}

export async function readLocalFollowupsData(search: string, signal?: AbortSignal): Promise<FollowupsData> {
  const response = await fetch(`/followups/unfulfilled/data${search}`, {credentials: 'same-origin',
    cache: 'no-store', signal, headers: {Accept: 'application/json'}});
  if (!response.ok) throw new Error(`本地名单刷新失败（${response.status}）`);
  return response.json();
}

export function UnfulfilledWriteForm({candidates, storeId, selectedIds, executeLimit,
  blocked, onLimitChange}: {
  candidates: UnfulfilledCandidate[]; storeId: string; selectedIds: number[];
  executeLimit: number; blocked: boolean; onLimitChange: (value: number) => void;
}) {
  const selectedRows = selectUnfulfilledRows(candidates, storeId, selectedIds);
  const count = unfulfilledExecutionCount(selectedRows.length, executeLimit);
  const disabled = blocked || count === 0;
  const storeName = selectedRows[0]?.store_name || storeId;
  return <form method="post" action={UNFULFILLED_JOB_ACTION}
    data-job-form data-unfulfilled-form data-submit-disabled={disabled ? 'true' : 'false'}
    data-job-label="D+15 · 写飞书未发布" data-confirm-token="y"
    data-confirm-title="确认写飞书未发布"
    data-confirm-description={unfulfilledConfirmationDescription(storeName, storeId, count)}
    data-confirm-action={`写飞书未发布（本次 ${count} 条）`}
    onSubmitCapture={(event) => {if (disabled) event.preventDefault();}}>
    {selectedRows.map((candidate) => <input key={candidate.task_id} type="hidden"
      name="task_ids" value={candidate.task_id} />)}
    <input type="hidden" name="execute_limit" value={executeLimit} />
    <Stack direction="horizontal" gap={3} vAlign="end" wrap="wrap">
      <NumberInput label="本次最多处理" value={executeLimit} min={1} step={1}
        isDisabled={blocked} onChange={onLimitChange} />
      <Text>已选 {selectedRows.length} 条</Text>
      <Button type="submit" variant="primary" isDisabled={disabled}
        label={`写飞书未发布（本次 ${count} 条）`} />
    </Stack>
  </form>;
}

export function UnfulfilledJobNotice({job}: {job: UnfulfilledJobScope}) {
  const active = ACTIVE_JOB_STATUSES.has(job.status);
  return <Stack gap={1}>
    <Text>{job.deduplicated ? '已复用原任务，新选择未另行执行。' : '服务器已冻结本次范围。'}
      店铺 {job.store_id}；实际任务 {job.task_ids.length} 条；上限 {job.execute_limit}；
      任务 ID：{job.task_ids.join('、') || '请查看任务详情'}。</Text>
    <Link href={`/jobs/${encodeURIComponent(job.job_id)}`}>
      {active ? '正在处理，查看任务' : '查看任务与逐行结果'}
    </Link>
    {!active && job.status !== 'succeeded' && <Text color="secondary">
      任务未完整成功；保留已知结果，不自动重试未知写入。
    </Text>}
  </Stack>;
}

function StageOperations({initial, showCandidates, onRowsRefresh}: {
  initial: UnfulfilledData; showCandidates: boolean; onRowsRefresh: (rows: FollowupRow[]) => void;
}) {
  const [localData, setLocalData] = useState(initial);
  const [storeId, setStoreId] = useState('');
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [executeLimit, setExecuteLimit] = useState(1);
  const [submitting, setSubmitting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [receipt, setReceipt] = useState<UnfulfilledJobScope | null>(null);
  const [readError, setReadError] = useState('');
  const hasActiveJobs = localData.jobs.some((job) => ACTIVE_JOB_STATUSES.has(job.status));

  useEffect(() => {
    let disposed = false;
    let reading = false;
    const controller = new AbortController();
    async function refreshLocalData() {
      if (reading) return;
      reading = true;
      setRefreshing(true);
      try {
        const refreshed = await readLocalFollowupsData(window.location.search, controller.signal);
        if (disposed) return;
        setLocalData(refreshed.unfulfilled);
        onRowsRefresh(refreshed.rows);
        setReceipt((previous) => {
          const current = refreshed.unfulfilled.jobs.find((job) => job.job_id === previous?.job_id);
          return previous && current ? {...current, deduplicated: previous.deduplicated} : previous;
        });
        setSelectedIds((previous) => previous.filter((identifier) =>
          refreshed.unfulfilled.candidates.some((candidate) => candidate.task_id === identifier)));
        setReadError('');
      } catch (error) {
        if (!disposed) setReadError(error instanceof Error ? error.message : '本地名单刷新失败');
      } finally {
        reading = false;
        if (!disposed) setRefreshing(false);
      }
    }
    function handleCreated(event: Event) {
      const {form, payload} = (event as CustomEvent).detail;
      if (!form?.matches('[data-unfulfilled-form]')) return;
      const frozen: UnfulfilledJobScope = {...payload, status: 'pending'};
      setReceipt(frozen);
      setSelectedIds([]);
      setLocalData((previous) => ({...previous, jobs: [frozen,
        ...previous.jobs.filter((job) => job.job_id !== frozen.job_id)]}));
    }
    function handleSubmission(event: Event) {
      const {form, submitting: inProgress} = (event as CustomEvent).detail;
      if (form?.matches('[data-unfulfilled-form]')) setSubmitting(inProgress);
    }
    function handleComplete(event: Event) {
      const job = (event as CustomEvent).detail;
      if (job?.job_type !== 'followup_unfulfilled_write') return;
      setReceipt((previous) => previous && previous.job_id === job.id
        ? {...previous, status: job.status} : previous);
      void refreshLocalData();
    }
    function handleSubmissionFailure(event: Event) {
      const {form} = (event as CustomEvent).detail;
      if (form?.matches('[data-unfulfilled-form]')) void refreshLocalData();
    }
    document.addEventListener('assistant:job-created', handleCreated);
    document.addEventListener('assistant:job-submit-state', handleSubmission);
    document.addEventListener('assistant:job-submit-failed', handleSubmissionFailure);
    document.addEventListener('assistant:job-complete', handleComplete);
    // Poll only while a local batch exists; selection and tab entry never inspect remote state.
    const timer = hasActiveJobs ? window.setInterval(refreshLocalData, 2000) : null;
    return () => {
      disposed = true;
      controller.abort();
      if (timer !== null) window.clearInterval(timer);
      document.removeEventListener('assistant:job-created', handleCreated);
      document.removeEventListener('assistant:job-submit-state', handleSubmission);
      document.removeEventListener('assistant:job-submit-failed', handleSubmissionFailure);
      document.removeEventListener('assistant:job-complete', handleComplete);
    };
  }, [hasActiveJobs, onRowsRefresh]);

  const stores = [...new Map(localData.candidates.map((candidate) => [candidate.store_id,
    {value: candidate.store_id, label: `${candidate.store_name || candidate.store_id}（${candidate.store_id}）`}])).values()];
  const storeCandidates = localData.candidates.filter((candidate) => candidate.store_id === storeId);
  const activeJob = localData.jobs.find((job) => job.store_id === storeId && ACTIVE_JOB_STATUSES.has(job.status));
  const blocked = submitting || refreshing || Boolean(activeJob) || Boolean(readError);
  const displayedJobs = receipt ? [receipt, ...localData.jobs.filter((job) => job.job_id !== receipt.job_id)]
    : localData.jobs;

  return <Section>
    <Stack gap={4}>
      <Heading level={2}>阶段操作</Heading>
      <Stack direction="horizontal" gap={6} wrap="wrap">
        <Stack gap={2}>
          <Heading level={3}>D+10 出名单</Heading>
          <Text color="secondary">只读查看当前 D+10 待办，不发送私信。</Text>
          <Button type="button" variant="secondary" label="查看 D+10 待办"
            onClick={() => window.location.assign('/followups?stage=day_10_list')} />
        </Stack>
        <Stack gap={2}>
          <Heading level={3}>D+15 未履约</Heading>
          <Text color="secondary">到货满15天仍未履约；只写飞书合作状态，不发私信</Text>
          <Button type="button" variant="secondary"
            label={`查看 D+15 待办（${localData.candidates.length} 条）`}
            onClick={() => window.location.assign('/followups?stage=unfulfilled')} />
        </Stack>
      </Stack>
      {displayedJobs.filter((job) => ACTIVE_JOB_STATUSES.has(job.status) || job.job_id === receipt?.job_id)
        .map((job) => <UnfulfilledJobNotice key={job.job_id} job={job} />)}
      {showCandidates && <Stack gap={3}>
        <Banner status="info" title="先更新本地待办"
          description="请先同步物流并生成今日待办。本名单只读本地数据，不代表刚刚实店核验；飞书仍为待发货时保留待办等待业务更新。" />
        {readError && <Banner status="error" title="本地名单未刷新" description={readError} />}
        <Selector label="选择本次店铺" placeholder="请明确选择店铺，不默认选择第一家"
          options={stores} value={storeId} isDisabled={submitting}
          onChange={(value) => {setStoreId(value); setSelectedIds([]);}} />
        <UnfulfilledWriteForm candidates={localData.candidates} storeId={storeId}
          selectedIds={selectedIds} executeLimit={executeLimit} blocked={blocked}
          onLimitChange={setExecuteLimit} />
        {localData.candidates.length === 0 ? <Text color="secondary">暂无可处理待办</Text>
          : storeId === '' ? <Text color="secondary">选择店铺后查看并勾选该店可处理待办，不允许跨店选择。</Text>
          : <>
            <CheckboxInput label="选择本店全部可处理待办" isDisabled={blocked || storeCandidates.length === 0}
              value={selectedIds.length === 0 ? false : selectedIds.length === storeCandidates.length ? true : 'indeterminate'}
              onChange={(checked) => setSelectedIds(checked ? storeCandidates.map((candidate) => candidate.task_id) : [])} />
            <Table data={storeCandidates as unknown as Record<string, unknown>[]} idKey="task_id"
              density="compact" hasHover columns={[
                {key: 'selected', header: '选择', width: proportional(1), renderCell: (row) => {
                  const candidate = row as unknown as UnfulfilledCandidate;
                  return <CheckboxInput label={`选择 ${candidate.creator_name} 的任务 ${candidate.task_id}`}
                    isLabelHidden value={selectedIds.includes(candidate.task_id)} isDisabled={blocked}
                    onChange={(checked) => setSelectedIds((previous) => checked
                      ? [...previous.filter((identifier) => identifier !== candidate.task_id), candidate.task_id]
                      : previous.filter((identifier) => identifier !== candidate.task_id))} />;
                }},
                {key: 'creator_name', header: '达人 / 任务', width: proportional(2), renderCell: (row) => {
                  const candidate = row as unknown as UnfulfilledCandidate;
                  return <Link href={`/followups/${candidate.task_id}`}>{candidate.creator_name} / {candidate.task_id}</Link>;
                }},
                {key: 'sample_product', header: '寄样产品', width: proportional(2)},
                {key: 'delivered_on', header: '送达日（北京）', width: proportional(2)},
                {key: 'days_since_delivery', header: '到货天数', width: proportional(1)},
                {key: 'feishu_cooperation_status', header: '缓存合作状态', width: proportional(2),
                  renderCell: (row) => <Text>{String(row.feishu_cooperation_status || '未缓存；写前复核')}</Text>},
                {key: 'record_id', header: '飞书记录', width: proportional(2),
                  renderCell: (row) => <Text>{String(row.record_id || '写前唯一匹配')}</Text>},
              ]} />
          </>}
        {displayedJobs.filter((job) => !ACTIVE_JOB_STATUSES.has(job.status) && job.job_id !== receipt?.job_id)
          .slice(0, 1).map((job) => <UnfulfilledJobNotice key={job.job_id} job={job} />)}
      </Stack>}
    </Stack>
  </Section>;
}

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
  const [rows, setRows] = useState(data.rows);
  const [stage, setStage] = useState(data.filters.stage);
  const [status, setStatus] = useState(data.filters.status);
  const [language, setLanguage] = useState(data.filters.language);
  const [platformStatus, setPlatformStatus] = useState(data.filters.platform_status);

  const withAllOption = (options: {value: string; label: string}[]) => [
    {value: '', label: '全部'},
    ...options,
  ];

  const rowNumbers = useMemo(
    () => new Map(rows.map((row, index) => [row.id, index + 1])),
    [rows],
  );

  return (
    <Stack gap={4}>
      <PageHeader
        eyebrow="本地预览"
        title="达人跟进"
        description="先跑物流与跟进动作生成待办，再预览、人工分类和本地完成标记；D+15 批量写飞书需输入 y 确认，默认最多 1 条，不发私信。"
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

      <StageOperations initial={data.unfulfilled ?? {candidates: [], jobs: []}}
        showCandidates={data.filters.stage === 'unfulfilled'} onRowsRefresh={setRows} />

      <Section>
        <OperatorGroups groups={data.operator_groups} />
      </Section>

      <Banner
        status="info"
        title="默认只读"
        description="被新阶段取代的旧步骤默认不出现；查看与选择不读写远端。单条写飞书仍需显式开关，D+15 批量写入须确认本次范围并输入 y。"
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

      {rows.length === 0 ? (
        <EmptyState
          title="没有匹配的跟进待办"
          description="先运行「生成今日跟进待办（只读）」，或放宽筛选条件。"
        />
      ) : (
        <Table
          data={rows as unknown as Record<string, unknown>[]}
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
