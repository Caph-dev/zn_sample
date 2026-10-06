import {Banner} from '@astryxdesign/core/Banner';
import {Button} from '@astryxdesign/core/Button';
import {EmptyState} from '@astryxdesign/core/EmptyState';
import {Heading} from '@astryxdesign/core/Heading';
import {Link} from '@astryxdesign/core/Link';
import {MetadataList, MetadataListItem} from '@astryxdesign/core/MetadataList';
import {Section} from '@astryxdesign/core/Section';
import {Stack} from '@astryxdesign/core/Stack';
import {Table, proportional} from '@astryxdesign/core/Table';
import {Text} from '@astryxdesign/core/Text';
import {useCallback, useEffect, useRef, useState} from 'react';

import {PageHeader} from '../components/PageHeader';
import {StatusToken} from '../components/StatusToken';
import type {
  CleanupBatch, CleanupBatchSummary, CleanupItem, CleanupMonths,
  CleanupRecentBatches, StatusTone, TargetCleanupData,
} from '../types';

const BATCHES_API = '/api/target-cleanup/batches';
const ITEM_PAGE_SIZE = 100;
const PREVIEW_MONTHS: CleanupMonths[] = [4, 2];
const ACTIVE_STATUSES = new Set(['pending', 'queued', 'running']);
const STATUS_LABELS: Record<string, string> = {
  queued: '等待执行', running: '正在执行', completed: '执行结束',
  incomplete: '扫描不完整', unclaimed: '尚未确认清理', partial: '部分处理',
  needs_review: '需要人工核对', pending: '待处理', attempting: '写入尝试中',
  submitted: '取消操作已提交', skipped: '已跳过', failed: '失败',
  uncertain: '结果不确定，需人工核对', not_processed: '未处理',
  succeeded: '任务结束', cancelled: '任务已取消', interrupted: '任务已中断',
};
const BLOCK_LABELS: Record<string, string> = {
  'preview-not-completed': '预览尚未完成或已失败，不能清理。',
  'incomplete-preview': '扫描不完整，保留已发现记录供核对，不能清理。',
  'empty-preview': '本次完整预览没有候选，不会创建清理任务。',
  'expired-preview': '预览已过期；如需清理，请明确发起新的只读预览。',
  'different-local-day': '已跨过预览冻结的本机日历日，不能沿用这份名单。',
  'batch-already-consumed': '本批次已被认领或执行，不可再次清理。',
  'historical-write-requires-review': '候选涉及历史已提交或不确定写入，须人工核对，不会自动重试。',
  'store-busy': '已有页面任务占用店铺；请等任务结束后只读刷新。',
};
const REASON_LABELS: Record<string, string> = {
  'before-cutoff': '上次修改早于截止日',
  'not-before-cutoff': '不早于截止日',
  'missing-invitation-id': '缺少计划 ID',
  'unparsed-modified-date': '修改日期无法解析',
};
const DOWNLOAD_LABELS = {
  scan_csv: '本次扫描 CSV', candidates_csv: '固定候选 CSV',
  results_csv: '逐项结果 CSV', backup_csv: '执行前备份 CSV',
};

export interface CleanupRequestMemory {
  key: string;
  attempted: boolean;
  confirmedBatchId: string;
}

/** Invalid/unavailable memory blocks submission rather than silently changing a request key. */
export function readCleanupRequest(storage: Pick<Storage, 'getItem'>, slot: string): CleanupRequestMemory | null {
  const stored = storage.getItem(`target-cleanup:${slot}`);
  if (stored === null) return null;
  const parsed = JSON.parse(stored);
  if (typeof parsed?.key !== 'string' || !parsed.key || parsed.key.length > 128
      || typeof parsed.attempted !== 'boolean' || typeof parsed.confirmedBatchId !== 'string') {
    throw new Error('Invalid cleanup request memory');
  }
  return parsed;
}

export function writeCleanupRequest(
  storage: Pick<Storage, 'setItem'>, slot: string, request: CleanupRequestMemory,
) {
  storage.setItem(`target-cleanup:${slot}`, JSON.stringify(request));
}

export interface CleanupViewState {
  batchId: string;
  offset: number;
  revision: number;
  batch: CleanupBatch | null;
  loading: boolean;
  error: string;
}
export type CleanupViewAction =
  | {type: 'select'; batchId: string; offset?: number}
  | {type: 'refresh'}
  | {type: 'loaded'; revision: number; batch: CleanupBatch}
  | {type: 'failed'; revision: number; message: string};

/** A late GET cannot resurrect a previous batch/page or a cleared preview. */
export function reduceCleanupView(state: CleanupViewState, action: CleanupViewAction): CleanupViewState {
  if (action.type === 'select') {
    return {batchId: action.batchId, offset: action.offset ?? 0, revision: state.revision + 1,
      batch: null, loading: Boolean(action.batchId), error: ''};
  }
  if (action.type === 'refresh') {
    return {...state, revision: state.revision + 1, loading: Boolean(state.batchId), error: ''};
  }
  if (action.revision !== state.revision) return state;
  if (action.type === 'failed') return {...state, loading: false, error: action.message};
  if (action.batch.batch_id !== state.batchId || action.batch.offset !== state.offset) return state;
  return {...state, batch: action.batch, loading: false, error: ''};
}

export function cleanupBatchUrl(batchId: string) {
  return batchId ? `/plan-cleanup?batch_id=${encodeURIComponent(batchId)}` : '/plan-cleanup';
}

export function cleanupConfirmationDescription(batch: CleanupBatchSummary) {
  return `店铺 ${batch.store_name || '未命名'}（storeId: ${batch.store_id}）；`
    + `${batch.months} 个自然月；上次修改日期 < ${batch.frozen?.cutoff || '未冻结'}（不含截止日）。`
    + `将处理本批次全部 ${batch.candidate_count} 条候选，其中 ${batch.nonzero_count} 条已接受或已推广人数不为零。`
    + '不要求人数为 0；不是默认只执行 1 条，不可脚本撤销。执行前仍会复核店铺与固定快照。';
}

function statusTone(status: string): StatusTone {
  if (['failed', 'uncertain', 'needs_review'].includes(status)) return 'error';
  if (['incomplete', 'partial', 'attempting'].includes(status)) return 'warning';
  if (['submitted', 'completed', 'succeeded'].includes(status)) return 'success';
  return ACTIVE_STATUSES.has(status) ? 'accent' : 'neutral';
}

function CleanupStatus({status, preview = false}: {status: string; preview?: boolean}) {
  return <StatusToken tone={statusTone(status)}
    label={preview && status === 'completed' ? '只读预览完成' : (STATUS_LABELS[status] || status)} />;
}

export async function readCleanupJson<Result>(path: string, signal?: AbortSignal): Promise<Result> {
  const response = await fetch(path, {signal, credentials: 'same-origin', cache: 'no-store',
    headers: {Accept: 'application/json'}});
  if (!response.ok) throw new Error(`只读查询失败（${response.status}）；不会自动重发任务。`);
  return response.json();
}

export async function readCleanupReceipt(slot: string, requestKey: string) {
  const payload = await readCleanupJson<CleanupRecentBatches>(
    `${BATCHES_API}?limit=20&offset=0&idempotency_key=${encodeURIComponent(requestKey)}`,
  );
  return payload.batches.find((batch) => slot === `preview:${batch.months}`
    || (slot === `execute:${batch.batch_id}` && Boolean(batch.execute_job_id)));
}

interface SubmissionIntent {
  slot: string;
  request: CleanupRequestMemory;
  revision: number;
  observer: MutationObserver;
}

export function TargetCleanupPage({data}: {data: TargetCleanupData}) {
  const [view, setView] = useState<CleanupViewState>({batchId: data.batch_id,
    offset: 0, revision: 0, batch: data.batch, loading: false, error: data.read_error});
  const viewReference = useRef(view);
  const [recent, setRecent] = useState(data.recent_batches);
  const [recentRevision, setRecentRevision] = useState(0);
  const [recentError, setRecentError] = useState('');
  const [months, setMonths] = useState<CleanupMonths>(data.batch?.months ?? data.default_months);
  const [requests, setRequests] = useState<Record<string, CleanupRequestMemory>>({});
  const requestReference = useRef(requests);
  const [storageError, setStorageError] = useState('');
  const [recoveryMessage, setRecoveryMessage] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const mounted = useRef(false);
  const submissions = useRef(new Map<HTMLFormElement, SubmissionIntent>());

  const transition = useCallback((action: CleanupViewAction) => {
    viewReference.current = reduceCleanupView(viewReference.current, action);
    setView(viewReference.current);
  }, []);

  const selectBatch = useCallback((batchId: string, replace = false, offset = 0) => {
    transition({type: 'select', batchId, offset});
    const path = cleanupBatchUrl(batchId);
    if (replace) window.history.replaceState(null, '', path);
    else window.history.pushState(null, '', path);
  }, [transition]);

  const rememberRequest = useCallback((slot: string, request: CleanupRequestMemory) => {
    try {
      writeCleanupRequest(window.sessionStorage, slot, request);
      requestReference.current = {...requestReference.current, [slot]: request};
      setRequests(requestReference.current);
      return true;
    } catch (_error) {
      setStorageError('无法保存请求标识，已禁止新提交。请保留批次 ID，使用只读刷新或任务页核对；不要清除存储后重试写入。');
      return false;
    }
  }, []);

  const ensureRequest = useCallback((slot: string) => {
    try {
      const request = readCleanupRequest(window.sessionStorage, slot)
        ?? {key: window.crypto.randomUUID(), attempted: false, confirmedBatchId: ''};
      return rememberRequest(slot, request) ? request : null;
    } catch (_error) {
      setStorageError('请求标识存储不可用或损坏，已禁止新提交；请只读核对已有批次，不会生成替代请求。');
      return null;
    }
  }, [rememberRequest]);

  const recoverRequest = useCallback(async (slot: string, request: CleanupRequestMemory, revision: number) => {
    try {
      const recovered = await readCleanupReceipt(slot, request.key);
      if (!mounted.current || requestReference.current[slot]?.key !== request.key) return;
      if (recovered) {
        rememberRequest(slot, {...request, confirmedBatchId: recovered.batch_id});
        setRecentRevision((previous) => previous + 1);
        if (viewReference.current.revision === revision) {
          setMonths(recovered.months);
          selectBatch(recovered.batch_id, true);
          setRecoveryMessage('已用原请求标识找回批次；仅查询状态，没有重新提交任务。');
        }
      } else if (viewReference.current.revision === revision) {
        setRecoveryMessage('尚未找到该请求的任务回执。请只读刷新核对；如明确再次提交，将保留同一请求标识，不会自动重试或换新请求。');
      }
    } catch (error) {
      if (mounted.current && viewReference.current.revision === revision) {
        setRecoveryMessage(error instanceof Error ? error.message : '请求回执读取失败；请只读刷新。');
      }
    }
  }, [rememberRequest, selectBatch]);

  useEffect(() => {
    mounted.current = true;
    for (const preset of PREVIEW_MONTHS) {
      const slot = `preview:${preset}`;
      const request = ensureRequest(slot);
      if (request?.attempted && !request.confirmedBatchId) {
        void recoverRequest(slot, request, viewReference.current.revision);
      }
    }
    return () => {
      mounted.current = false;
      submissions.current.forEach((intent) => intent.observer.disconnect());
      submissions.current.clear();
    };
  }, [ensureRequest, recoverRequest]);

  useEffect(() => {
    if (!view.batchId) return;
    const slot = `execute:${view.batchId}`;
    const request = ensureRequest(slot);
    if (request?.attempted && !request.confirmedBatchId) {
      void recoverRequest(slot, request, viewReference.current.revision);
    }
  }, [view.batchId, ensureRequest, recoverRequest]);

  useEffect(() => {
    if (!view.batchId || !data.database_ready) return;
    const controller = new AbortController();
    void readCleanupJson<CleanupBatch>(
      `${BATCHES_API}/${encodeURIComponent(view.batchId)}?limit=${ITEM_PAGE_SIZE}&offset=${view.offset}`,
      controller.signal,
    ).then((batch) => {
      if (controller.signal.aborted) return;
      transition({type: 'loaded', revision: view.revision, batch});
      if (viewReference.current.batch === batch) setMonths(batch.months);
    }).catch((error) => {
      if (!controller.signal.aborted) transition({type: 'failed', revision: view.revision,
        message: error instanceof Error ? error.message : '批次读取失败；请只读刷新。'});
    });
    return () => controller.abort();
  }, [view.batchId, view.offset, view.revision, data.database_ready, transition]);

  useEffect(() => {
    if (!data.database_ready) return;
    const controller = new AbortController();
    void readCleanupJson<CleanupRecentBatches>(`${BATCHES_API}?limit=20&offset=0`, controller.signal)
      .then((payload) => { if (!controller.signal.aborted) { setRecent(payload); setRecentError(''); } })
      .catch((error) => { if (!controller.signal.aborted) setRecentError(String(error.message || error)); });
    return () => controller.abort();
  }, [recentRevision, data.database_ready]);

  useEffect(() => {
    const batch = view.batch;
    if (!batch || view.error || view.loading) return;
    const active = Object.values(batch.jobs).some((job) => ACTIVE_STATUSES.has(job.status));
    if (!active && !batch.can_execute) return;
    // One bounded page per poll; completed/blocked batches stop polling.
    const timer = window.setTimeout(() => transition({type: 'refresh'}), 5000);
    return () => window.clearTimeout(timer);
  }, [view.batch, view.error, view.loading, transition]);

  useEffect(() => {
    function observeSubmission(event: Event) {
      const form = event.target;
      if (!(form instanceof HTMLFormElement) || !form.hasAttribute('data-target-cleanup-form')) return;
      if (submissions.current.size > 0 || storageError) { event.preventDefault(); return; }
      const slot = form.dataset.cleanupSlot || '';
      const request = requestReference.current[slot];
      if (!request || !rememberRequest(slot, {...request, attempted: true})) { event.preventDefault(); return; }
      if (slot.startsWith('preview:')) {
        setMonths(Number(form.dataset.cleanupMonths) as CleanupMonths);
        selectBatch('', true);
      }
      setSubmitting(true);
      setRecoveryMessage('提交结果将按原请求标识核对；断网后不会自动重发。');
      // app.js remains the only submitter. Its finally branch also covers a cancelled y dialog.
      const intent: SubmissionIntent = {slot, request: {...request, attempted: true},
        revision: viewReference.current.revision, observer: new MutationObserver(() => {
          if (form.dataset.submitting !== 'false') return;
          intent.observer.disconnect();
          submissions.current.delete(form);
          setSubmitting(false);
          void recoverRequest(intent.slot, intent.request, intent.revision);
        })};
      submissions.current.set(form, intent);
      intent.observer.observe(form, {attributes: true, attributeFilter: ['data-submitting']});
    }
    function created(event: Event) {
      const {payload, form} = (event as CustomEvent).detail || {};
      if (!(form instanceof HTMLFormElement) || !form.hasAttribute('data-target-cleanup-form')) return;
      const intent = submissions.current.get(form);
      if (!intent || typeof payload?.batch_id !== 'string') return;
      intent.observer.disconnect();
      submissions.current.delete(form);
      setSubmitting(false);
      rememberRequest(intent.slot, {...intent.request, confirmedBatchId: payload.batch_id});
      setRecentRevision((previous) => previous + 1);
      if (viewReference.current.revision === intent.revision) {
        selectBatch(payload.batch_id, true);
        setRecoveryMessage('任务已创建；清理结论以本页批次数据为准，不把任务结束当成平台最终取消确认。');
      }
    }
    function completed(event: Event) {
      const job = (event as CustomEvent).detail;
      const batch = viewReference.current.batch;
      if (!batch || ![batch.preview_job_id, batch.execute_job_id].includes(job?.id)) return;
      transition({type: 'refresh'});
      setRecentRevision((previous) => previous + 1);
    }
    function historyChanged() {
      transition({type: 'select', batchId: new URL(window.location.href).searchParams.get('batch_id') || ''});
    }
    document.addEventListener('submit', observeSubmission, true);
    document.addEventListener('assistant:job-created', created);
    document.addEventListener('assistant:job-complete', completed);
    window.addEventListener('popstate', historyChanged);
    return () => {
      document.removeEventListener('submit', observeSubmission, true);
      document.removeEventListener('assistant:job-created', created);
      document.removeEventListener('assistant:job-complete', completed);
      window.removeEventListener('popstate', historyChanged);
    };
  }, [storageError, rememberRequest, recoverRequest, selectBatch, transition]);

  function refreshReadOnly() {
    transition({type: 'refresh'});
    setRecentRevision((previous) => previous + 1);
    for (const [slot, request] of Object.entries(requestReference.current)) {
      if (request.attempted && !request.confirmedBatchId) {
        void recoverRequest(slot, request, viewReference.current.revision);
      }
    }
  }

  function prepareNextPreview(preset: CleanupMonths) {
    const slot = `preview:${preset}`;
    if (!requestReference.current[slot]?.confirmedBatchId || submitting) return;
    if (!rememberRequest(slot, {key: window.crypto.randomUUID(), attempted: false, confirmedBatchId: ''})) return;
    setMonths(preset);
    selectBatch('', true);
    setRecoveryMessage('新的只读请求已准备好；请再点预览按钮开始。此操作本身不会扫描或清理。');
  }

  const batch = view.batch;
  const executionRequest = batch ? requests[`execute:${batch.batch_id}`] : undefined;
  const executeVisible = batch?.can_execute === true && !view.error && !view.loading && !submitting;

  return (
    <Stack gap={4}>
      <PageHeader eyebrow="定向合作 · 进行中" title="计划清理"
        description="只读预览后再确认处理固定名单。打开、刷新或返回此页只查询本地批次，不自动扫描。"
        actions={<Stack direction="horizontal" gap={2}>
          <Button type="button" label="只读刷新" variant="secondary" onClick={refreshReadOnly} />
          <Link href={data.prepare_href} isStandalone>运行准备</Link>
        </Stack>} />

      <Banner status="warning" title="会纳入有人接受或推广的旧计划，取消不可脚本撤销"
        description="来源是 TikTok Shop 定向合作 → 进行中，不是样品申请。按上次修改日期判断，不要求已接受 / 已推广为 0。确认后处理该批次全部候选，不是默认只执行 1 条；不要与旧项目脚本并跑。" />
      {(view.error || recentError || storageError) && <Banner status="error" title="只读核对需要处理"
        description={[view.error, recentError, storageError].filter(Boolean).join(' ')} />}
      {recoveryMessage && <Banner status="info" title="请求回执" description={recoveryMessage} />}

      <Section>
        <Stack gap={3}>
          <Heading level={2}>1. 选择只读预览</Heading>
          <Text type="supporting">默认四个月；两个月覆盖更大的日期范围，并不更安全。当前查看 {months} 个月口径。预览与执行须绑定同一唯一运行店铺，本页不探活、不切店。</Text>
          <Stack direction="horizontal" gap={2} wrap="wrap">
            {PREVIEW_MONTHS.map((preset) => {
              const slot = `preview:${preset}`;
              const request = requests[slot];
              const monthLabel = preset === 4 ? '四' : '两';
              return <form key={preset} method="post" action="/api/target-cleanup/previews"
                data-job-form data-target-cleanup-form="preview" data-cleanup-slot={slot}
                data-cleanup-months={preset} data-job-label={`计划清理 · ${preset} 个月只读预览`}>
                <input type="hidden" name="months" value={String(preset)} />
                <input type="hidden" name="idempotency_key" value={request?.key || ''} />
                {request?.confirmedBatchId ? <Button type="button" variant="secondary"
                  label={`新建${monthLabel}个月预览（只读）`} isDisabled={submitting || Boolean(storageError)}
                  onClick={() => prepareNextPreview(preset)} />
                  : <Button type="submit" variant={preset === 4 ? 'primary' : 'secondary'}
                    label={`预览${monthLabel}个月前计划（只读）`}
                    isDisabled={!data.database_ready || !request || submitting || Boolean(storageError)} />}
              </form>;
            })}
          </Stack>
          <Text type="supporting">同一请求已获服务端回执后，点「新建」只准备新请求，再点「预览」才会扫描。响应丢失时保留原标识，先查回执，绝不自动新建或重试。</Text>
        </Stack>
      </Section>

      {view.batchId && <Text type="code">批次 ID：{view.batchId}</Text>}
      {view.loading && <Text type="supporting">正在读取所选批次；此时不提供清理入口。</Text>}
      {!batch ? <EmptyState title={view.batchId ? '等待批次只读数据' : '尚未选择预览批次'}
        description="明确点击上方只读预览，或从下方近期批次找回已有名单；不会自动开始扫描。" /> : <>
        <Section>
          <Stack gap={3}>
            <Heading level={2}>2. 固定名单与扫描证据</Heading>
            <MetadataList columns="multi">
              <MetadataListItem label="绑定店铺">{batch.store_name || '未命名'} / {batch.store_id}</MetadataListItem>
              <MetadataListItem label="平台店铺">{batch.shop_id} / {batch.shop_region}</MetadataListItem>
              <MetadataListItem label="冻结本机运行日">{batch.frozen?.run_date || '尚未冻结'}</MetadataListItem>
              <MetadataListItem label="冻结时区 / 偏移">{batch.frozen ? `${batch.frozen.timezone_name} / ${batch.frozen.offset_minutes} 分钟` : '尚未冻结'}</MetadataListItem>
              <MetadataListItem label="冻结启动 UTC">{batch.frozen?.started_at || '尚未冻结'}</MetadataListItem>
              <MetadataListItem label="截止日（不含）">{batch.frozen?.cutoff || '尚未冻结'} / {batch.months} 个自然月</MetadataListItem>
              <MetadataListItem label="预览有效至（服务器）">{batch.expires_at || '扫描完成后冻结'}</MetadataListItem>
              <MetadataListItem label="预览状态"><CleanupStatus status={batch.preview_status} preview /></MetadataListItem>
              <MetadataListItem label="扫描完整性">{batch.scan_complete ? '完整' : '不完整 / 尚未完成'}</MetadataListItem>
              <MetadataListItem label="停止原因">{batch.stop_reason || '尚未返回'}</MetadataListItem>
              <MetadataListItem label="本次扫描">{batch.scan_count} 条 / {batch.pages_scanned} 页（非全店全量声明）</MetadataListItem>
              <MetadataListItem label="固定候选">{batch.candidate_count} 条</MetadataListItem>
              <MetadataListItem label="已接受 / 已推广非零">{batch.nonzero_count} 条候选</MetadataListItem>
            </MetadataList>
            {batch.error_code && <Banner status="error" title={`批次需核对：${batch.error_code}`}
              description={batch.error_summary || '请查看已知结果与任务日志，不要自动重试。'} />}
            <Text type="supporting">last_modified 是平台原文；modified_date 是快照解析结果，均只读。表格仅分页展示固定候选，不可勾选或改变执行范围；排除项见本次扫描 CSV。</Text>
            {batch.items.length === 0 ? <EmptyState isCompact title="当前页没有候选记录"
              description="以扫描完整性和服务端阻断原因为准；空表不代表成功清理。" /> : <Table
              data={batch.items as unknown as Record<string, unknown>[]} idKey="invitation_id" density="compact" hasHover
              columns={[
                {key: 'name', header: '名称', width: proportional(2)},
                {key: 'invitation_id', header: '计划 ID', width: proportional(2)},
                {key: 'last_modified', header: '上次修改原文', width: proportional(2)},
                {key: 'modified_date', header: '快照日期', width: proportional(2)},
                {key: 'accepted_count', header: '已接受', renderCell: (row) => <Text hasTabularNumbers>{String(row.accepted_count ?? '未知')}</Text>},
                {key: 'promoted_count', header: '已推广', renderCell: (row) => <Text hasTabularNumbers>{String(row.promoted_count ?? '未知')}</Text>},
                {key: 'reason', header: '判定理由', width: proportional(2), renderCell: (row) => <Text>{REASON_LABELS[String(row.reason)] || String(row.reason)}</Text>},
                {key: 'status', header: '处理状态', width: proportional(2), renderCell: (row) => <CleanupStatus status={String(row.status)} />},
                {key: 'summary', header: '结果依据', width: proportional(2), renderCell: (row) => {
                  const item = row as unknown as CleanupItem;
                  return <Text>{item.summary || item.action || '-'}</Text>;
                }},
              ]} />}
            <Stack direction="horizontal" gap={2} wrap="wrap">
              <Button type="button" variant="secondary" label="上一页候选" isDisabled={view.loading || batch.offset === 0}
                onClick={() => selectBatch(batch.batch_id, true, Math.max(0, batch.offset - ITEM_PAGE_SIZE))} />
              <Text type="supporting">显示 {batch.total ? batch.offset + 1 : 0}–{batch.offset + batch.items.length} / {batch.total} 条；每页最多 100 条</Text>
              <Button type="button" variant="secondary" label="下一页候选" isDisabled={view.loading || batch.offset + batch.items.length >= batch.total}
                onClick={() => selectBatch(batch.batch_id, true, batch.offset + ITEM_PAGE_SIZE)} />
            </Stack>
          </Stack>
        </Section>

        <Section>
          <Stack gap={3}>
            <Heading level={2}>3. 确认与逐项结果</Heading>
            <CleanupStatus status={batch.execute_status} />
            {executeVisible ? <form method="post" action={`${BATCHES_API}/${encodeURIComponent(batch.batch_id)}/execute`}
              data-job-form data-target-cleanup-form="execute" data-cleanup-slot={`execute:${batch.batch_id}`}
              data-job-label="计划清理 · 提交取消操作" data-confirm-token="y"
              data-confirm-title={`确认清理固定名单中的 ${batch.candidate_count} 条计划`}
              data-confirm-description={cleanupConfirmationDescription(batch)} data-confirm-action="清理全部固定候选">
              <input type="hidden" name="idempotency_key" value={executionRequest?.key || ''} />
              <Button type="submit" variant="destructive" label={`确认清理这 ${batch.candidate_count} 条`}
                isDisabled={!executionRequest || Boolean(storageError)} />
            </form> : <Text>{view.error || view.loading || submitting ? '正在提交或只读状态尚未确认，暂不提供清理入口。'
              : BLOCK_LABELS[batch.execute_block_reason] || `服务端禁止执行：${batch.execute_block_reason || '未提供执行许可'}`}</Text>}
            <MetadataList orientation="horizontal">
              {Object.entries(batch.counts).map(([status, count]) => <MetadataListItem key={status}
                label={STATUS_LABELS[status] || status}>{count} 条</MetadataListItem>)}
            </MetadataList>
            <Text type="supporting">「取消操作已提交」不是平台最终确认取消。部分处理、未处理或不确定结果需人工核对；页面不提供自动重试或强制重试。</Text>
            <Stack direction="horizontal" gap={2} wrap="wrap">
              {(['preview', 'execute'] as const).map((phase) => {
                const job = batch.jobs[phase];
                return job.job_id ? <Stack key={phase} direction="horizontal" gap={1}>
                  <Link href={`/jobs/${encodeURIComponent(job.job_id)}`}>{phase === 'preview' ? '预览任务详情' : '清理任务详情'}</Link>
                  <CleanupStatus status={job.status || 'pending'} />
                  <Button type="button" variant="secondary" label="查看全局进度" onClick={() => document.dispatchEvent(
                    new CustomEvent('assistant:monitor-job', {detail: {jobId: job.job_id}}),
                  )} />
                </Stack> : null;
              })}
              {Object.entries(DOWNLOAD_LABELS).map(([artifact, label]) => {
                const link = batch.downloads[artifact as keyof typeof DOWNLOAD_LABELS];
                if (!link || (artifact === 'results_csv' && !batch.results_csv_ready)) return null;
                return <Link key={artifact} href={link}>{label}</Link>;
              })}
            </Stack>
            {!batch.results_csv_ready && batch.execute_status !== 'unclaimed' && <Text type="supporting">结果 CSV 尚不可用；逐项数据库证据仍保留在本页，旧结果文件不会当成最新报告展示。</Text>}
          </Stack>
        </Section>
      </>}

      <Section>
        <Stack gap={3}>
          <Heading level={2}>近期批次</Heading>
          <Text type="supporting">最近 20 批 / 共 {recent.total} 批。选择只改变批次 URL 并读取数据，不创建任务。</Text>
          {recent.batches.length === 0 ? <EmptyState isCompact title="尚无批次记录"
            description="明确发起一次只读预览后，可在此找回进度与导出。" /> : <Table
            data={recent.batches as unknown as Record<string, unknown>[]} idKey="batch_id" density="compact" hasHover
            columns={[
              {key: 'batch_id', header: '批次', width: proportional(3), renderCell: (row) => {
                const summary = row as unknown as CleanupBatchSummary;
                return <Link href={cleanupBatchUrl(summary.batch_id)} onClick={(event) => {
                  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                  event.preventDefault(); selectBatch(summary.batch_id); setMonths(summary.months);
                }}>{summary.batch_id}</Link>;
              }},
              {key: 'store_name', header: '绑定店铺', width: proportional(2)},
              {key: 'months', header: '自然月'},
              {key: 'candidate_count', header: '候选'},
              {key: 'nonzero_count', header: '人数非零'},
              {key: 'preview_status', header: '预览', width: proportional(2), renderCell: (row) => <CleanupStatus status={String(row.preview_status)} preview />},
              {key: 'execute_status', header: '处理', width: proportional(2), renderCell: (row) => <CleanupStatus status={String(row.execute_status)} />},
              {key: 'created_at', header: '创建时间', width: proportional(2)},
            ]} />}
        </Stack>
      </Section>
    </Stack>
  );
}
