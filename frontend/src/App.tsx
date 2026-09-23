import {AppShell} from '@astryxdesign/core/AppShell';
import {Banner} from '@astryxdesign/core/Banner';
import {Stack} from '@astryxdesign/core/Stack';
import {useCallback, useEffect, useMemo, useRef, useState} from 'react';

import {
  createExecution,
  createPreview,
  getExecution,
  getJobEvents,
  getOptions,
  getPreview,
  getStoreExecutions,
  getRecent,
  getStorePreparation,
  saveRuleDraft,
  JobEvent,
} from './api';
import {ExecutionPanel} from './components/ExecutionPanel';
import {AppNavigation} from './components/AppNavigation';
import {OrderBackfillPanel} from './components/OrderBackfillPanel';
import {ReadinessPanel} from './components/ReadinessPanel';
import {ResultsPanel} from './components/ResultsPanel';
import {RuleConfigPanel} from './components/RuleConfigPanel';
import {StandardScreenPanel} from './components/StandardScreenPanel';
import {buildExecutionIdempotencyKey} from './executionIdempotency';
import {getApprovalProgress} from './executionProgress';
import {isValidExecutionLimit, loadBrowserExecutionLimit, rememberBrowserExecutionLimit} from './executionLimitMemory';
import {buildStandardRule, hasOutdatedSkuEvidence, normalizeRule, rulesEqual, validateDraft} from './ruleModel';
import {loadBrowserRuleMemory, rememberBrowserRule} from './ruleMemory';
import type {
  AutoApprovalBootstrap,
  ExecutionPayload,
  ExecutionRecord,
  OptionsPayload,
  PreviewPayload,
  RuleDraft,
  StoreSummary,
} from './types';

const POLL_INTERVAL_MS = 2000;

function isJobActive(jobStatus: string | undefined): boolean {
  return jobStatus === 'pending' || jobStatus === 'running';
}

function errorMessage(error: unknown): string {
  if (error && typeof error === 'object' && 'message' in error) {
    return String((error as {message: unknown}).message);
  }
  return String(error);
}

function notifyJobMonitor(jobId: string | undefined) {
  if (!jobId) {
    return;
  }
  document.dispatchEvent(
    new CustomEvent('assistant:monitor-job', {detail: {jobId}}),
  );
}

export function App({bootstrap}: {bootstrap: AutoApprovalBootstrap}) {
  const [rememberedForm] = useState(loadBrowserRuleMemory);
  const [options, setOptions] = useState<OptionsPayload | null>(null);
  const [optionsError, setOptionsError] = useState('');
  const [rule, setRule] = useState<RuleDraft>(() =>
    rememberedForm ? normalizeRule(rememberedForm.rule) : buildStandardRule(null),
  );
  const [displayMode, setDisplayMode] = useState<'standard' | 'custom'>(
    rememberedForm?.displayMode ?? 'standard',
  );
  const [ruleEdited, setRuleEdited] = useState(false);
  const [preview, setPreview] = useState<PreviewPayload | null>(null);
  const [previewEvents, setPreviewEvents] = useState<JobEvent[]>([]);
  const [previewCreating, setPreviewCreating] = useState(false);
  const [previewError, setPreviewError] = useState('');
  const [selection, setSelection] = useState<string[]>([]);
  const [execution, setExecution] = useState<ExecutionPayload | null>(null);
  const [executionRecords, setExecutionRecords] = useState<ExecutionRecord[] | null>(null);
  const [executing, setExecuting] = useState(false);
  const [executionError, setExecutionError] = useState('');
  const [limit, setLimit] = useState(loadBrowserExecutionLimit);
  const [writeFeishu, setWriteFeishu] = useState(true);
  const [confirmText, setConfirmText] = useState('');
  const [storeSummary, setStoreSummary] = useState<StoreSummary | null>(null);

  const updateExecutionLimit = useCallback((nextLimit: number) => {
    setLimit(nextLimit);
    if (isValidExecutionLimit(nextLimit) && !rememberBrowserExecutionLimit(nextLimit)) {
      setOptionsError('浏览器无法保存本次限量；当前设置仍有效，但刷新后可能恢复为默认值。');
    }
  }, []);

  const previewPollTimer = useRef<number | null>(null);
  const formRestoredRef = useRef(rememberedForm !== null);

  const busy = storeSummary?.error === 'ziniao-busy';
  const skuEvidenceOutdated = preview !== null && hasOutdatedSkuEvidence(preview);

  const previewInvalidated = useMemo(() => {
    if (preview === null) {
      return false;
    }
    return skuEvidenceOutdated || !rulesEqual(rule, preview.rule);
  }, [rule, preview, skuEvidenceOutdated]);
  const approvalProgress = useMemo(
    () => getApprovalProgress(executionRecords ?? []),
    [executionRecords],
  );
  const operationActive = executing || isJobActive(execution?.job?.status) || executionRecords?.some(
    (record) => record.status === 'queued' || record.status === 'running',
  ) === true;

  const refreshStore = useCallback(() => {
    getStorePreparation()
      .then(setStoreSummary)
      .catch(() => setStoreSummary({ok: false, error: 'running-query-failed'}));
  }, []);

  const pollPreview = useCallback(async () => {
    if (preview === null) {
      return;
    }
    try {
      const payload = await getPreview(preview.preview_id);
      setPreview(payload);
      if (payload.job && isJobActive(payload.job.status)) {
        getJobEvents(payload.job.job_id, 0)
          .then(({events}) => setPreviewEvents(events))
          .catch(() => {});
      }
    } catch {
      // 网络异常先查任务状态，不重复创建任务。
    }
  }, [preview]);

  const pollExecution = useCallback(async () => {
    if (execution === null) {
      return;
    }
    try {
      const payload = await getExecution(execution.execution_id);
      setExecution(payload);
      if (!isJobActive(payload.job?.status)) {
        setExecutionRecords(null);
        setExecutionRecords(await getStoreExecutions(payload.store_id));
        setExecution(null);
      }
    } catch {
      // 网络异常先查任务状态。
    }
  }, [execution]);

  useEffect(() => {
    const optionsRequest = getOptions()
      .then((payload) => {
        setOptions(payload);
        if (!payload.hero.ok) {
          setOptionsError('主推款表不可用，请检查飞书配置。');
        }
        // 当前浏览器记忆或用户刚做的修改优先，其次才是服务端草稿。
        if (payload.saved_rule !== null && !formRestoredRef.current) {
          formRestoredRef.current = true;
          setRule(normalizeRule(payload.saved_rule.rule, payload.categories));
          setDisplayMode('custom');
        }
      })
      .catch((error) => setOptionsError(errorMessage(error)));
    refreshStore();
    getRecent()
      .then(({previews}) => {
        const latestPreview = previews[0];
        if (latestPreview !== undefined) {
          getPreview(latestPreview.preview_id)
            .then(async (payload) => {
              setPreview(payload);
              // 旧结果只恢复结果区；没有记忆和草稿时才用其规则兜底。
              await optionsRequest;
              if (!formRestoredRef.current) {
                formRestoredRef.current = true;
                setRule(normalizeRule(payload.rule));
                setDisplayMode('custom');
              }
            })
            .catch(() => {});
        }
      })
      .catch(() => {});
    return () => {
      if (previewPollTimer.current !== null) {
        window.clearInterval(previewPollTimer.current);
      }
    };
  }, [refreshStore]);

  useEffect(() => {
    if (preview === null) {
      return undefined;
    }
    let cancelled = false;
    setExecutionRecords(null);
    setExecution(null);
    getStoreExecutions(preview.store_id)
      .then((records) => {
        if (cancelled) {
          return;
        }
        setExecutionRecords(records);
        const active = records.find((record) => record.status === 'queued' || record.status === 'running');
        if (active !== undefined) {
          getExecution(active.execution_id).then((payload) => {
            if (!cancelled) {
              setExecution(payload);
            }
          }).catch(() => {});
        }
      })
      .catch(() => {
        if (!cancelled) {
          setExecutionError('无法读取操作记录，请刷新页面后再选择。');
        }
      });
    return () => { cancelled = true; };
  }, [preview?.preview_id]);

  useEffect(() => {
    if (executionRecords !== null) {
      setSelection((current) => {
        const remaining = current.filter((applyId) => !approvalProgress.has(applyId));
        return remaining.length === current.length ? current : remaining;
      });
    }
  }, [approvalProgress, executionRecords]);

  // 自定义规则改动后自动保存为草稿（仅保存校验通过的规则，供下次回填）。
  useEffect(() => {
    if (!ruleEdited || displayMode !== 'custom' || options === null) {
      return undefined;
    }
    if (validateDraft(rule, options).length > 0) {
      return undefined;
    }
    const saveTimer = window.setTimeout(() => {
      saveRuleDraft(rule).catch(() => {
        // 草稿保存失败不影响当前操作，下次改动会重试。
      });
    }, 800);
    return () => window.clearTimeout(saveTimer);
  }, [rule, displayMode, options, ruleEdited]);

  useEffect(() => {
    if (previewPollTimer.current !== null) {
      window.clearInterval(previewPollTimer.current);
      previewPollTimer.current = null;
    }
    if (preview !== null && (isJobActive(preview.job?.status) || preview.status === 'completed')) {
      previewPollTimer.current = window.setInterval(() => {
        void pollPreview();
      }, isJobActive(preview.job?.status) ? POLL_INTERVAL_MS : 60000);
    }
    return () => {
      if (previewPollTimer.current !== null) {
        window.clearInterval(previewPollTimer.current);
        previewPollTimer.current = null;
      }
    };
  }, [preview, pollPreview]);

  useEffect(() => {
    if (execution !== null && isJobActive(execution.job?.status)) {
      const timer = window.setInterval(() => {
        void pollExecution();
      }, POLL_INTERVAL_MS);
      return () => window.clearInterval(timer);
    }
    return undefined;
  }, [execution, pollExecution]);

  useEffect(() => {
    if (preview !== null && isJobActive(preview.job?.status)) {
      notifyJobMonitor(preview.job?.job_id || preview.job_id);
    }
  }, [preview]);

  useEffect(() => {
    if (execution !== null && isJobActive(execution.job?.status)) {
      notifyJobMonitor(execution.job?.job_id || execution.job_id);
    }
  }, [execution]);

  const updateRememberedForm = useCallback(
    (next: RuleDraft, mode: 'standard' | 'custom') => {
      // 修改规则使旧结果失效：由 previewInvalidated 统一判定。
      // 组逻辑固定 either、类目固定全选（UI 已移除选择框）。
      const normalizedRule = normalizeRule(next, options?.categories);
      formRestoredRef.current = true;
      setRuleEdited(true);
      setRule(normalizedRule);
      setDisplayMode(mode);
      // 同步写入，不等待 800ms 服务端保存；立即刷新也能保留未完成的编辑。
      if (!rememberBrowserRule({rule: normalizedRule, displayMode: mode})) {
        setOptionsError('浏览器无法保存条件记忆；合法规则仍会尝试保存到本机服务，请勿立即关闭页面。');
      }
    },
    [options],
  );

  const onStartPreview = useCallback(async () => {
    if (previewCreating) {
      return;
    }
    setPreviewCreating(true);
    setPreviewError('');
    try {
      const created = await createPreview(rule, storeSummary?.store?.storeId ?? null);
      notifyJobMonitor(created.job_id);
      const payload = await getPreview(created.preview_id);
      setSelection([]);
      setExecution(null);
      setExecutionRecords(null);
      setConfirmText('');
      setPreview(payload);
    } catch (error) {
      setPreviewError(errorMessage(error));
    } finally {
      setPreviewCreating(false);
    }
  }, [previewCreating, rule, storeSummary]);

  const onExecute = useCallback(async () => {
    if (preview === null || executionRecords === null || operationActive) {
      return;
    }
    setExecuting(true);
    setExecutionError('');
    try {
      const idempotencyKey = buildExecutionIdempotencyKey({
        previewId: preview.preview_id,
        applyIds: selection,
        limit,
        writeFeishu,
      });
      const created = await createExecution({
        preview_id: preview.preview_id,
        apply_ids: selection,
        limit,
        write_feishu: writeFeishu,
        confirmation: confirmText,
        idempotency_key: idempotencyKey,
      });
      notifyJobMonitor(created.job_id);
      setExecutionRecords(null);
      const payload = await getExecution(created.execution_id);
      setExecution(payload);
      setExecutionRecords(await getStoreExecutions(preview.store_id));
      setSelection([]);
      setConfirmText('');
    } catch (error) {
      setExecutionError(errorMessage(error));
      // 创建请求可能已经成功；仅恢复当前选择对应的执行，避免把旧记录误当作本次操作。
      if (preview !== null) {
        getStoreExecutions(preview.store_id)
          .then((records) => {
            setExecutionRecords(records);
            const match = records.find((record) =>
              record.preview_id === preview.preview_id
              && (record.status === 'queued' || record.status === 'running')
              && record.items.map((item) => item.apply_id).join(',') === selection.join(','),
            );
            if (match !== undefined) {
              return getExecution(match.execution_id).then((payload) => {
                setExecution(payload);
                setSelection([]);
                setConfirmText('');
              });
            }
            return null;
          })
          .catch(() => {});
      }
    } finally {
      setExecuting(false);
    }
  }, [preview, executionRecords, operationActive, selection, limit, writeFeishu, confirmText]);

  return (
    <AppShell
      height="auto"
      variant="section"
      contentPadding={4}
      sideNav={
        <AppNavigation activePath="/auto-approval" subheading="自动批准 · 自定义审核方案" />
      }
    >
      <Stack gap={4}>
        {optionsError !== '' && (
          <ReadinessBanner message={optionsError} />
        )}
        {previewEvents.length > 0 && preview !== null && isJobActive(preview.job?.status) && (
          <TaskProgress
            label={`筛查任务 ${preview.preview_id}`}
            events={previewEvents}
          />
        )}
        {bootstrap.screen_group !== null && (
          <StandardScreenPanel group={bootstrap.screen_group} />
        )}
        <ReadinessPanel
          storeSummary={storeSummary}
          busy={busy}
          prepareHref={bootstrap.prepare_href}
        />
        <RuleConfigPanel
          options={options}
          rule={rule}
          displayMode={displayMode}
          onDisplayModeChange={(mode) => updateRememberedForm(rule, mode)}
          onCopyToCustom={() => {
            updateRememberedForm(buildStandardRule(options), 'custom');
          }}
          onRuleChange={(next) => updateRememberedForm(next, displayMode)}
          onStartPreview={() => void onStartPreview()}
          previewError={previewError}
          previewRunning={
            previewCreating || (preview !== null && isJobActive(preview.job?.status))
          }
          busy={busy}
          previewInvalidated={previewInvalidated}
        />
        {preview !== null && preview.status === 'completed' && (
          <ResultsPanel
            preview={preview}
            selection={selection}
            onSelectionChange={setSelection}
            approvalProgress={approvalProgress}
            operationActive={operationActive}
            recordsReady={executionRecords !== null}
            previewInvalidated={previewInvalidated}
            staleReason={skuEvidenceOutdated
              ? '旧结果缺少有效的 B005 SKU 限制证据，请重新筛查后再批准。'
              : '规则或阈值已修改，旧结果已失效。'}
          />
        )}
        <ExecutionPanel
          preview={preview !== null && preview.status === 'completed' ? preview : null}
          previewInvalidated={previewInvalidated}
          selection={selection}
          approvalProgress={approvalProgress}
          limit={limit}
          onLimitChange={updateExecutionLimit}
          writeFeishu={writeFeishu}
          onWriteFeishuChange={setWriteFeishu}
          confirmText={confirmText}
          onConfirmTextChange={setConfirmText}
          onExecute={() => void onExecute()}
          executing={executing}
          operationActive={operationActive}
          recordsReady={executionRecords !== null}
          executionError={executionError}
          executionRecords={(executionRecords ?? []).filter(
            (record) => record.preview_id === preview?.preview_id,
          )}
        />
        <OrderBackfillPanel storeId={storeSummary?.store?.storeId ?? null} />
      </Stack>
    </AppShell>
  );
}

function ReadinessBanner({message}: {message: string}) {
  return <Banner status="warning" title="配置提示" description={message} />;
}

function TaskProgress({label, events}: {label: string; events: JobEvent[]}) {
  const lastEvent = events[events.length - 1];
  return (
    <Banner
      status="info"
      title={label}
      description={lastEvent !== undefined ? lastEvent.message : '任务执行中…'}
    />
  );
}
