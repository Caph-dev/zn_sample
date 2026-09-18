import {Banner} from '@astryxdesign/core/Banner';
import {Button} from '@astryxdesign/core/Button';
import {Heading} from '@astryxdesign/core/Heading';
import {MetadataList, MetadataListItem} from '@astryxdesign/core/MetadataList';
import {Section} from '@astryxdesign/core/Section';
import {Selector} from '@astryxdesign/core/Selector';
import {Stack} from '@astryxdesign/core/Stack';
import {StatusDot} from '@astryxdesign/core/StatusDot';
import {Table, proportional} from '@astryxdesign/core/Table';
import type {TableColumn} from '@astryxdesign/core/Table';
import {Text} from '@astryxdesign/core/Text';
import {TextInput} from '@astryxdesign/core/TextInput';
import {useCallback, useEffect, useRef, useState} from 'react';

import {getExecution, getExecutions, reconcileExecution} from '../api';
import {approvalActivity} from '../reconciliationState';
import type {ExecutionPayload, ExecutionSummary, ReconciliationItem} from '../types';

const HISTORY_PAGE_SIZE = 20;
const POLL_INTERVAL_MS = 2000;

const STATUS_LABELS: Record<string, string> = {
  confirmed: '平台已回读确认',
  'still-pending': '平台仍待审核',
  unknown: '未知，待核对',
  'identity-mismatch': '身份不匹配',
  error: '核对异常',
  'not-approved': '未批准',
  verified: '已回读确认',
  missing: '缺失',
  conflict: '存在冲突',
  unavailable: '暂未取得订单号',
  needs_review: '需人工核对',
  created: '接口返回成功，待回读确认',
};

function formatStatus(status: string): string {
  return STATUS_LABELS[status] ?? (status || '未知');
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

const REPORT_COLUMNS: TableColumn<ReconciliationItem>[] = [
  {
    key: 'creator_name', header: '达人 / 申请 / 商品', width: proportional(2),
    renderCell: (item) => (
      <Stack gap={1}>
        <Text>{item.creator_name || '未知达人'}</Text>
        <Text type="supporting">申请：{item.apply_id}</Text>
        <Text type="supporting">商品：{item.product_id}</Text>
      </Stack>
    ),
  },
  {
    key: 'platform_status', header: '平台', width: proportional(1),
    renderCell: (item) => formatStatus(item.platform_status),
  },
  {
    key: 'relation_status', header: '达人关系主键', width: proportional(1),
    renderCell: (item) => (
      <Stack gap={1}>
        <Text>{formatStatus(item.relation_status)}</Text>
        <Text type="supporting">记录：{item.record_id || '无'}</Text>
      </Stack>
    ),
  },
  {
    key: 'order_status', header: '订单号', width: proportional(1),
    renderCell: (item) => (
      <Stack gap={1}>
        <Text>{formatStatus(item.order_status)}</Text>
        <Text type="supporting">{item.order_no || '无订单号'}</Text>
      </Stack>
    ),
  },
  {
    key: 'status', header: '核对结论', width: proportional(1),
    renderCell: (item) => (
      <Stack direction="horizontal" gap={1} vAlign="center">
        <StatusDot
          variant={item.status === 'verified' ? 'success' : 'warning'}
          label={formatStatus(item.status)}
        />
        <Text>{formatStatus(item.status)}</Text>
      </Stack>
    ),
  },
  {
    key: 'detail', header: '说明 / 补写资格', width: proportional(2),
    renderCell: (item) => (
      <Stack gap={1}>
        <Text>{item.detail || '无补充说明'}</Text>
        <Text type="supporting">{item.can_repair ? '可补写缺失项' : '不可自动补写'}</Text>
      </Stack>
    ),
  },
];

export function ReconciliationPanel({currentExecutionId}: {currentExecutionId: string | null}) {
  const [history, setHistory] = useState<ExecutionSummary[]>([]);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState('');
  const [selectedId, setSelectedId] = useState('');
  const [selectionVersion, setSelectionVersion] = useState(0);
  const [execution, setExecution] = useState<ExecutionPayload | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [actionError, setActionError] = useState('');
  const [recoveringIds, setRecoveringIds] = useState<string[]>([]);

  const selectedIdRef = useRef('');
  const historyRequestRef = useRef(0);
  const detailRequestRef = useRef(0);
  const submittingRef = useRef(false);
  const recoveringIdsRef = useRef(new Set<string>());
  const expectedJobIdsRef = useRef(new Map<string, string>());

  const selectExecution = useCallback((executionId: string) => {
    if (executionId === selectedIdRef.current) {
      return;
    }
    // Invalidate immediately: even A -> B -> A must not accept the old A response.
    detailRequestRef.current += 1;
    selectedIdRef.current = executionId;
    setSelectedId(executionId);
    setSelectionVersion((version) => version + 1);
    setExecution(null);
    setDetailLoading(false);
    setDetailError('');
    setActionError('');
    setConfirmation('');
  }, []);

  const refreshHistory = useCallback(async (nextOffset: number) => {
    const requestVersion = ++historyRequestRef.current;
    setHistoryLoading(true);
    setHistoryError('');
    try {
      const payload = await getExecutions(nextOffset, HISTORY_PAGE_SIZE);
      if (requestVersion !== historyRequestRef.current) {
        return;
      }
      setHistory(payload.executions);
      setOffset(nextOffset);
      setHasMore(payload.has_more);
      if (!selectedIdRef.current && payload.executions[0]) {
        selectExecution(payload.executions[0].execution_id);
      }
    } catch (error) {
      if (requestVersion === historyRequestRef.current) {
        setHistoryError(formatError(error));
      }
    } finally {
      if (requestVersion === historyRequestRef.current) {
        setHistoryLoading(false);
      }
    }
  }, [selectExecution]);

  const refreshExecution = useCallback(async (executionId: string) => {
    if (!executionId || executionId !== selectedIdRef.current || submittingRef.current) {
      return;
    }
    const requestVersion = ++detailRequestRef.current;
    setDetailLoading(true);
    try {
      const payload = await getExecution(executionId);
      if (requestVersion !== detailRequestRef.current || selectedIdRef.current !== executionId) {
        return;
      }
      setExecution(payload);
      setDetailError('');
      const expectedJobId = expectedJobIdsRef.current.get(executionId);
      if (!expectedJobId || payload.reconciliation?.latest_job?.job_id === expectedJobId) {
        expectedJobIdsRef.current.delete(executionId);
        recoveringIdsRef.current.delete(executionId);
        setRecoveringIds([...recoveringIdsRef.current]);
      }
    } catch (error) {
      if (requestVersion === detailRequestRef.current && selectedIdRef.current === executionId) {
        setDetailError(formatError(error));
      }
    } finally {
      if (requestVersion === detailRequestRef.current) {
        setDetailLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    void refreshHistory(0);
    return () => {
      historyRequestRef.current += 1;
      detailRequestRef.current += 1;
    };
  }, [refreshHistory]);

  useEffect(() => {
    if (currentExecutionId) {
      selectExecution(currentExecutionId);
      void refreshHistory(0);
    }
  }, [currentExecutionId, refreshHistory, selectExecution]);

  useEffect(() => {
    void refreshExecution(selectedId);
  }, [selectedId, selectionVersion, refreshExecution]);

  const reconciliation = execution?.reconciliation;
  const latestJob = reconciliation?.latest_job;
  const report = reconciliation?.report;
  const recovering = recoveringIds.includes(selectedId);
  const reconciliationActive = isJobActive(latestJob?.status);
  const {active: approvalActive, stateError} = approvalActivity(execution);
  const shouldPoll = reconciliationActive || approvalActive || recovering || detailError !== '';

  useEffect(() => {
    if (!selectedId || !shouldPoll || submitting || detailLoading) {
      return undefined;
    }
    const timer = window.setTimeout(() => void refreshExecution(selectedId), POLL_INTERVAL_MS);
    return () => window.clearTimeout(timer);
  }, [selectedId, shouldPoll, submitting, detailLoading, refreshExecution]);

  const controlsBusy = submitting || recovering || reconciliationActive || approvalActive || detailLoading;
  const canVerify = Boolean(reconciliation?.available) && !controlsBusy && !detailError && !stateError;
  const canRepair = canVerify && Boolean(reconciliation?.can_repair);

  const startReconciliation = async (writeFeishu: boolean) => {
    if (selectedId !== selectedIdRef.current || submittingRef.current ||
      recoveringIdsRef.current.has(selectedId) || !canVerify ||
      (writeFeishu && (!canRepair || confirmation.trim().toLowerCase() !== 'y'))) {
      return;
    }
    const executionId = selectedId;
    submittingRef.current = true;
    detailRequestRef.current += 1;
    recoveringIdsRef.current.add(executionId);
    setRecoveringIds([...recoveringIdsRef.current]);
    setSubmitting(true);
    setDetailLoading(false);
    setActionError('');
    setConfirmation('');
    try {
      const created = await reconcileExecution(executionId, {
        write_feishu: writeFeishu,
        confirmation: writeFeishu ? 'y' : '',
      });
      expectedJobIdsRef.current.set(executionId, created.job_id);
      document.dispatchEvent(new CustomEvent('assistant:monitor-job', {
        detail: {jobId: created.job_id},
      }));
    } catch (error) {
      if (selectedIdRef.current === executionId) {
        setActionError(`请求结果未确认：${formatError(error)}。先重新读取任务状态，不会自动重复提交。`);
      }
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
      // No POST retry: keep actions locked until a subsequent status read succeeds.
      await refreshExecution(selectedIdRef.current);
    }
  };

  const historyOptions = history.map((batch) => ({
    value: batch.execution_id,
    label: `${formatTimestamp(batch.created_at)} · ${batch.execution_id}`,
    description: `店铺 ${batch.store_id} · ${batch.status}`,
  }));
  if (selectedId && !history.some((batch) => batch.execution_id === selectedId)) {
    historyOptions.unshift({value: selectedId, label: selectedId, description: '当前选中批次（不在本页历史中）'});
  }

  let unavailableReason = '';
  if (!selectedId) {
    unavailableReason = historyLoading ? '正在读取历史批次。' : '暂无执行批次；完成批准后可在这里核对。';
  } else if (recovering) {
    unavailableReason = '正在确认刚才请求的任务状态；确认前禁止再次核对或补写。';
  } else if (detailError) {
    unavailableReason = '状态读取失败，正在重查；恢复前不允许提交。';
  } else if (submitting || reconciliationActive || approvalActive) {
    unavailableReason = '任务尚在提交或执行中，请等待完成，不要重复操作。';
  } else if (stateError) {
    unavailableReason = stateError;
  } else if (detailLoading) {
    unavailableReason = '正在刷新批次状态。';
  } else if (!reconciliation?.available) {
    unavailableReason = reconciliation?.blocked_reason || '当前批次尚不可核对，或服务端未提供核对能力。';
  }

  return (
    <Section>
      <Stack gap={3}>
        <Heading level={2}>5 · 核对与补写</Heading>
        <Text type="supporting">
          历史批次独立保留，不受重新筛查或修改规则影响。核对范围为达人关系主键和订单号，不改合作状态/物流，不重新批准、不发私信。
        </Text>
        <Selector
          label="选择历史执行批次"
          options={historyOptions}
          value={selectedId || undefined}
          onChange={(executionId) => {
            if (!submittingRef.current) {
              selectExecution(executionId);
            }
          }}
          placeholder="暂无执行批次"
          isDisabled={submitting || historyOptions.length === 0}
        />
        <Stack direction="horizontal" gap={2} wrap="wrap" vAlign="center">
          <Button label="上一页" isDisabled={historyLoading || submitting || offset === 0}
            onClick={() => void refreshHistory(Math.max(0, offset - HISTORY_PAGE_SIZE))} />
          <Text type="supporting">第 {Math.floor(offset / HISTORY_PAGE_SIZE) + 1} 页 · 每页 {HISTORY_PAGE_SIZE} 条</Text>
          <Button label="下一页" isDisabled={historyLoading || submitting || !hasMore}
            onClick={() => void refreshHistory(offset + HISTORY_PAGE_SIZE)} />
          <Button label="刷新批次与状态" isLoading={historyLoading}
            isDisabled={submitting || detailLoading}
            onClick={() => {
              void refreshHistory(offset);
              void refreshExecution(selectedId);
            }} />
        </Stack>
        {historyError && <Banner status="error" title="历史批次读取失败" description={historyError} />}
        {detailError && <Banner status="error" title="批次状态读取失败" description={detailError} />}
        {actionError && <Banner status="warning" title="请先核实任务状态" description={actionError} />}
        {execution && (
          <MetadataList columns="multi">
            <MetadataListItem label="执行批次">{execution.execution_id}</MetadataListItem>
            <MetadataListItem label="店铺">{execution.store_id}</MetadataListItem>
            <MetadataListItem label="批准批次状态">{execution.status}</MetadataListItem>
            <MetadataListItem label="执行限量">{execution.limit} 条</MetadataListItem>
            <MetadataListItem label="原批准写飞书设置">{execution.write_feishu ? '开启' : '关闭（仍可单独确认补写）'}</MetadataListItem>
            <MetadataListItem label="创建时间">{formatTimestamp(execution.created_at)}</MetadataListItem>
          </MetadataList>
        )}
        {execution?.error_summary && <Banner status="error" title="原批准批次异常" description={execution.error_summary} />}
        {unavailableReason && <Text type="supporting">{unavailableReason}</Text>}
        <Stack direction="horizontal" gap={2} wrap="wrap" vAlign="end">
          <Button label="只读核对" variant="secondary" isDisabled={!canVerify}
            onClick={() => void startReconciliation(false)} />
          <TextInput label="输入 y 确认补写缺失项（写飞书）" value={confirmation}
            onChange={setConfirmation} placeholder="y" isDisabled={!canRepair} />
          <Button label="补写缺失项" variant="primary"
            isDisabled={!canRepair || confirmation.trim().toLowerCase() !== 'y'}
            onClick={() => void startReconciliation(true)} />
        </Stack>
        <Text type="supporting">
          只读核对无需确认，不写飞书。补写目标：达人关系管理(新) / 达人管理总表；仅补缺失项，不覆盖冲突值，沿用本批次执行限量。
        </Text>
        {!canRepair && (
          <Text type="supporting">
            补写暂不可用：{unavailableReason || reconciliation?.repair_blocked_reason || '没有可自动补写的缺失项，请先只读核对。'}
          </Text>
        )}
        {latestJob && (
          <Stack gap={1}>
            <Text>最近核对/补写任务：{latestJob.job_id} · {latestJob.status}</Text>
            <Text type="supporting">{latestJob.progress_message || '暂无进度说明'}</Text>
            {(latestJob.error_summary || latestJob.error_code || ['failed', 'interrupted', 'cancelled'].includes(latestJob.status)) && (
              <Banner status="error" title="最近核对/补写任务未成功"
                description={[latestJob.error_code, latestJob.error_summary].filter(Boolean).join('：') || `任务状态：${latestJob.status}，请查看任务日志。`} />
            )}
          </Stack>
        )}
        {report ? (
          <Stack gap={2}>
            <Heading level={3}>最近回读报告</Heading>
            <Text type="supporting">
              核对时间：{formatTimestamp(report.checked_at)} · {report.write_feishu ? '补写任务报告（以逐行回读结论为准）' : '只读核对'}
              {reconciliationActive || recovering ? ' · 当前任务尚未结束，以下为已保存报告。' : ''}
            </Text>
            {report.items.length > 0
              ? <Table data={report.items} columns={REPORT_COLUMNS} idKey="apply_id" density="compact" />
              : <Text type="supporting">本次回读报告没有可核对条目。</Text>}
          </Stack>
        ) : (
          <Text type="supporting">尚未回读核对；任务完成不代表飞书已写入</Text>
        )}
        {!report && execution?.items.some((item) => item.feishu_relation_status === 'created') && (
          <Text type="supporting">历史飞书 created 状态仅表示：接口返回成功，待回读确认。</Text>
        )}
      </Stack>
    </Section>
  );
}
