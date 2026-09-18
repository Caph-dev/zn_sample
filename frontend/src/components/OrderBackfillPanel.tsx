import {Banner} from '@astryxdesign/core/Banner';
import {Button} from '@astryxdesign/core/Button';
import {Heading} from '@astryxdesign/core/Heading';
import {MetadataList, MetadataListItem} from '@astryxdesign/core/MetadataList';
import {Section} from '@astryxdesign/core/Section';
import {Stack} from '@astryxdesign/core/Stack';
import {StatusDot} from '@astryxdesign/core/StatusDot';
import {Table, proportional} from '@astryxdesign/core/Table';
import type {TableColumn} from '@astryxdesign/core/Table';
import {Text} from '@astryxdesign/core/Text';
import {TextInput} from '@astryxdesign/core/TextInput';
import {useCallback, useEffect, useRef, useState} from 'react';

import {
  getOrderBackfillCandidates,
  getOrderBackfillState,
  startOrderBackfill,
} from '../api';
import type {
  OrderBackfillCandidate,
  OrderBackfillItem,
  OrderBackfillStatePayload,
} from '../types';

const POLL_INTERVAL_MS = 2000;

const ITEM_STATUS_LABELS: Record<string, string> = {
  matched: '后台已给出订单号',
  written: '已写入并回读一致',
  unchanged: '飞书已有相同订单号',
  conflict: '飞书已有不同订单号，转人工',
  'write-uncertain': '写入结果未知，转人工',
  'no-platform-order': '后台暂无订单号',
  ambiguous: '后台存在多个订单号，转人工',
  planned: '仅核对未写入',
  'skipped-limit': '超出限量，未写入',
};

function formatStatus(status: string): string {
  return ITEM_STATUS_LABELS[status] ?? (status || '未知');
}

function isJobActive(status: string | undefined): boolean {
  return status === 'pending' || status === 'running';
}

function formatError(error: unknown): string {
  return error && typeof error === 'object' && 'message' in error
    ? String(error.message)
    : String(error);
}

function formatTimestamp(timestamp: string | null | undefined): string {
  if (!timestamp) {
    return '尚无记录';
  }
  const parsed = new Date(timestamp);
  return Number.isNaN(parsed.getTime()) ? timestamp : parsed.toLocaleString('zh-CN');
}

function itemTone(status: string): 'success' | 'warning' | 'error' | 'neutral' {
  if (status === 'written' || status === 'unchanged') {
    return 'success';
  }
  if (status === 'conflict' || status === 'write-uncertain' || status === 'ambiguous') {
    return 'error';
  }
  if (status === 'matched' || status === 'no-platform-order' || status === 'planned') {
    return 'warning';
  }
  return 'neutral';
}

const CANDIDATE_COLUMNS: TableColumn<OrderBackfillCandidate>[] = [
  {
    key: 'creator_handle', header: '达人（红人ID）', width: proportional(2),
    renderCell: (row) => (
      <Stack gap={1}>
        <Text>{row.creator_handle}</Text>
        <Text type="supporting">记录：{row.record_id}</Text>
      </Stack>
    ),
  },
  {
    key: 'sample_product', header: '寄样产品', width: proportional(1),
    renderCell: (row) => row.sample_product || '未填',
  },
  {
    key: 'cooperation_status', header: '合作状态', width: proportional(1),
    renderCell: (row) => row.cooperation_status || '未填',
  },
  {
    key: 'created_at', header: '记录创建时间', width: proportional(1),
    renderCell: (row) => formatTimestamp(row.created_at),
  },
];

const REPORT_COLUMNS: TableColumn<OrderBackfillItem>[] = [
  {
    key: 'creator_handle', header: '达人（红人ID）', width: proportional(2),
    renderCell: (row) => (
      <Stack gap={1}>
        <Text>{row.creator_handle || '未知达人'}</Text>
        <Text type="supporting">寄样产品：{row.sample_product || '未填'}</Text>
      </Stack>
    ),
  },
  {
    key: 'order_no', header: '订单号', width: proportional(1),
    renderCell: (row) => (
      <Stack gap={1}>
        <Text>{row.order_no || row.current_order || '无'}</Text>
        <Text type="supporting">来源：{row.platform_tab || '—'}</Text>
      </Stack>
    ),
  },
  {
    key: 'status', header: '结论', width: proportional(2),
    renderCell: (row) => (
      <Stack direction="horizontal" gap={1} vAlign="center">
        <StatusDot variant={itemTone(row.status)} label={formatStatus(row.status)} />
        <Text>{formatStatus(row.status)}</Text>
      </Stack>
    ),
  },
  {
    key: 'detail', header: '说明', width: proportional(2),
    renderCell: (row) => row.detail || '无补充说明',
  },
];

export function OrderBackfillPanel({storeId}: {storeId: string | null}) {
  const [state, setState] = useState<OrderBackfillStatePayload | null>(null);
  const [stateError, setStateError] = useState('');
  const [candidates, setCandidates] = useState<OrderBackfillCandidate[]>([]);
  const [candidatesTotal, setCandidatesTotal] = useState(0);
  const [candidatesScannedAt, setCandidatesScannedAt] = useState('');
  const [candidatesLoading, setCandidatesLoading] = useState(false);
  const [candidatesError, setCandidatesError] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [actionError, setActionError] = useState('');

  const submittingRef = useRef(false);

  const refreshState = useCallback(async () => {
    try {
      const payload = await getOrderBackfillState();
      setState(payload);
      setStateError('');
    } catch (error) {
      setStateError(formatError(error));
    }
  }, []);

  const refreshCandidates = useCallback(async () => {
    setCandidatesLoading(true);
    try {
      const payload = await getOrderBackfillCandidates();
      setCandidates(payload.rows);
      setCandidatesTotal(payload.total);
      setCandidatesScannedAt(payload.scanned_at);
      setCandidatesError('');
    } catch (error) {
      setCandidatesError(formatError(error));
    } finally {
      setCandidatesLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshState();
    void refreshCandidates();
  }, [refreshState, refreshCandidates]);

  const latestJob = state?.latest_job;
  const jobActive = isJobActive(latestJob?.status);
  const controlsBusy = submitting || jobActive || candidatesLoading;
  const canSubmit = Boolean(state?.available) && !controlsBusy;

  useEffect(() => {
    if (!jobActive || submitting) {
      return undefined;
    }
    const timer = window.setInterval(() => {
      void refreshState();
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [jobActive, submitting, refreshState]);

  // 任务结束后候选集合已变化：重新扫一次飞书，避免页面停留在旧名单。
  const finishedJobRef = useRef('');
  useEffect(() => {
    const jobId = latestJob?.job_id || '';
    if (!jobId || jobActive || finishedJobRef.current === jobId) {
      return;
    }
    finishedJobRef.current = jobId;
    void refreshCandidates();
  }, [jobActive, latestJob?.job_id, refreshCandidates]);

  const onSubmit = async () => {
    if (submittingRef.current || !canSubmit || confirmation.trim().toLowerCase() !== 'y') {
      return;
    }
    submittingRef.current = true;
    setSubmitting(true);
    setActionError('');
    setConfirmation('');
    try {
      const created = await startOrderBackfill({
        store_id: storeId,
        limit: state?.limit_default ?? 0,
        confirmation: 'y',
      });
      document.dispatchEvent(new CustomEvent('assistant:monitor-job', {
        detail: {jobId: created.job_id},
      }));
    } catch (error) {
      setActionError(`请求结果未确认：${formatError(error)}。先刷新任务状态，不会自动重复提交。`);
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
      await refreshState();
    }
  };

  const report = state?.report ?? null;
  const blockedReason = state?.blocked_reason || '';
  const lookbackHours = state?.lookback_hours ?? 72;
  const person = state?.person ?? '王良希（技术）';

  return (
    <Section>
      <Stack gap={3}>
        <Heading level={2}>5 · 订单号补写</Heading>
        <Text type="supporting">
          按固定规则直接核对并补写飞书「订单号」列：人员 = {person}、记录创建时间在近 {lookbackHours} 小时内、
          且订单号为空。写入前会对每条候选到商家后台按达人 ID 逐个搜索（待发货 → 已发货 → 处理中 → 已完成 → 待审核），
          取该申请的订单号；只写「订单号」一列，不新建记录、不改合作状态或物流，已有不同订单号不覆盖。重复运行幂等。
        </Text>
        <MetadataList columns="multi">
          <MetadataListItem label="扫描范围">人员 {person} · 近 {lookbackHours} 小时 · 订单号为空</MetadataListItem>
          <MetadataListItem label="目标表格">达人关系管理(新) / 达人管理总表</MetadataListItem>
          <MetadataListItem label="订单号来源">商家后台搜索结果（main_order_id）</MetadataListItem>
          <MetadataListItem label="执行限量">
            {(state?.limit_default ?? 0) > 0
              ? `${state?.limit_default} 条`
              : '不限量（按当前扫描结果）'}
          </MetadataListItem>
        </MetadataList>
        {stateError && <Banner status="error" title="状态读取失败" description={stateError} />}
        {candidatesError && <Banner status="error" title="飞书扫描失败" description={candidatesError} />}
        <Stack direction="horizontal" gap={2} wrap="wrap" vAlign="center">
          <Text>
            近 {lookbackHours} 小时缺「订单号」的记录：{candidatesTotal} 行
          </Text>
          <Button label="重新扫描" variant="secondary" isLoading={candidatesLoading}
            isDisabled={controlsBusy} onClick={() => void refreshCandidates()} />
          <Text type="supporting">上次扫描：{formatTimestamp(candidatesScannedAt)}</Text>
        </Stack>
        {candidates.length > 0
          ? <Table data={candidates} columns={CANDIDATE_COLUMNS} idKey="record_id" density="compact" />
          : <Text type="supporting">
            {candidatesLoading ? '正在读取飞书…' : '当前没有匹配的记录（可能都已补写完成）。'}
          </Text>}
        {blockedReason && <Text type="supporting">{blockedReason}</Text>}
        <Stack direction="horizontal" gap={2} wrap="wrap" vAlign="end">
          <TextInput label="输入 y 确认补写订单号（写飞书）" value={confirmation}
            onChange={setConfirmation} placeholder="y" isDisabled={!canSubmit} />
          <Button label="补写订单号" variant="primary"
            isDisabled={!canSubmit || confirmation.trim().toLowerCase() !== 'y'}
            isLoading={submitting} onClick={() => void onSubmit()} />
        </Stack>
        <Text type="supporting">
          不需要先跑只读核对，也不选批次：每次点击都按上面的规则重新扫描飞书。
          运行中不要重复点击；写任务不可取消。
        </Text>
        {actionError && <Banner status="warning" title="请先核实任务状态" description={actionError} />}
        {latestJob?.job_id && (
          <Stack gap={1}>
            <Text>最近补写任务：{latestJob.job_id} · {latestJob.status}</Text>
            <Text type="supporting">{latestJob.progress_message || '暂无进度说明'}</Text>
            {(latestJob.error_summary || latestJob.error_code ||
              ['failed', 'interrupted', 'cancelled'].includes(latestJob.status || '')) && (
              <Banner status="error" title="最近补写任务未成功"
                description={[latestJob.error_code, latestJob.error_summary].filter(Boolean).join('：')
                  || `任务状态：${latestJob.status}，请查看任务日志。`} />
            )}
          </Stack>
        )}
        {report ? (
          <Stack gap={2}>
            <Heading level={3}>最近回读报告</Heading>
            <Text type="supporting">
              核对时间：{formatTimestamp(report.checked_at)} · 店铺 {report.store_id} ·
              后台搜索命中 {report.platform_rows} 行 ·
              {report.write_feishu ? '本次为写入任务' : '本次仅核对'}
            </Text>
            {(report.search_errors ?? []).length > 0 && (
              <Banner status="warning" title="部分后台搜索失败"
                description={(report.search_errors ?? []).join('；')} />
            )}
            {report.items.length > 0
              ? <Table data={report.items} columns={REPORT_COLUMNS} idKey="record_id" density="compact" />
              : <Text type="supporting">本次没有需要处理的记录。</Text>}
          </Stack>
        ) : (
          <Text type="supporting">还没有跑过补写；任务完成不代表飞书已写入，以回读报告为准。</Text>
        )}
      </Stack>
    </Section>
  );
}
