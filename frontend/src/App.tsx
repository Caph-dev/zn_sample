import {Banner} from '@astryxdesign/core/Banner';
import {Stack} from '@astryxdesign/core/Stack';
import {useCallback, useEffect, useMemo, useRef, useState} from 'react';

import {
  createEnvironmentCheck,
  createExecution,
  createPreview,
  getExecution,
  getJobEvents,
  getOptions,
  getPreview,
  getRecent,
  getStorePreparation,
  reconcileExecution,
  JobEvent,
} from './api';
import {ExecutionPanel} from './components/ExecutionPanel';
import {ReadinessPanel} from './components/ReadinessPanel';
import {ResultsPanel} from './components/ResultsPanel';
import {RuleConfigPanel} from './components/RuleConfigPanel';
import {TopNav} from './components/TopNav';
import {buildStandardRule} from './ruleModel';
import type {
  ExecutionPayload,
  OptionsPayload,
  PreviewPayload,
  RuleDraft,
  StoreSummary,
} from './types';

const POLL_INTERVAL_MS = 2000;
const STORE_POLL_INTERVAL_MS = 5000;

function isJobActive(jobStatus: string | undefined): boolean {
  return jobStatus === 'pending' || jobStatus === 'running';
}

function errorMessage(error: unknown): string {
  if (error && typeof error === 'object' && 'message' in error) {
    return String((error as {message: unknown}).message);
  }
  return String(error);
}

function ruleEquals(left: RuleDraft, right: RuleDraft): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

export function App() {
  const [options, setOptions] = useState<OptionsPayload | null>(null);
  const [optionsError, setOptionsError] = useState('');
  const [rule, setRule] = useState<RuleDraft>(() => buildStandardRule(null));
  const [displayMode, setDisplayMode] = useState<'standard' | 'custom'>('standard');
  const [preview, setPreview] = useState<PreviewPayload | null>(null);
  const [previewEvents, setPreviewEvents] = useState<JobEvent[]>([]);
  const [previewCreating, setPreviewCreating] = useState(false);
  const [previewError, setPreviewError] = useState('');
  const [selection, setSelection] = useState<string[]>([]);
  const [execution, setExecution] = useState<ExecutionPayload | null>(null);
  const [executing, setExecuting] = useState(false);
  const [executionError, setExecutionError] = useState('');
  const [reconciling, setReconciling] = useState(false);
  const [reconcileError, setReconcileError] = useState('');
  const [limit, setLimit] = useState(1);
  const [writeFeishu, setWriteFeishu] = useState(false);
  const [confirmText, setConfirmText] = useState('');
  const [reconcileConfirmText, setReconcileConfirmText] = useState('');
  const [storeSummary, setStoreSummary] = useState<StoreSummary | null>(null);
  const [checkingEnvironment, setCheckingEnvironment] = useState(false);
  const [environmentNote, setEnvironmentNote] = useState('');

  const previewPollTimer = useRef<number | null>(null);
  const storePollTimer = useRef<number | null>(null);

  const busy = storeSummary?.error === 'ziniao-busy';

  const previewInvalidated = useMemo(() => {
    if (preview === null) {
      return false;
    }
    return !ruleEquals(rule, preview.rule);
  }, [rule, preview]);

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
      setExecution(await getExecution(execution.execution_id));
    } catch {
      // 网络异常先查任务状态。
    }
  }, [execution]);

  useEffect(() => {
    getOptions()
      .then((payload) => {
        setOptions(payload);
        if (!payload.hero.ok) {
          setOptionsError('主推款表不可用，请检查飞书配置。');
        }
      })
      .catch((error) => setOptionsError(errorMessage(error)));
    refreshStore();
    getRecent()
      .then(({previews, executions}) => {
        const latestPreview = previews[0];
        const latestExecution = executions[0];
        if (latestPreview !== undefined) {
          getPreview(latestPreview.preview_id)
            .then((payload) => {
              setPreview(payload);
              setRule(payload.rule);
              setDisplayMode('custom');
              if (payload.status === 'completed') {
                setLimit(Math.max(1, payload.stats.eligible > 0 ? 1 : 1));
              }
            })
            .catch(() => {});
        }
        if (latestExecution !== undefined) {
          getExecution(latestExecution.execution_id)
            .then(setExecution)
            .catch(() => {});
        }
      })
      .catch(() => {});
    const storeTimer = window.setInterval(refreshStore, STORE_POLL_INTERVAL_MS);
    storePollTimer.current = storeTimer;
    return () => {
      window.clearInterval(storeTimer);
      if (previewPollTimer.current !== null) {
        window.clearInterval(previewPollTimer.current);
      }
    };
  }, [refreshStore]);

  useEffect(() => {
    if (previewPollTimer.current !== null) {
      window.clearInterval(previewPollTimer.current);
      previewPollTimer.current = null;
    }
    if (preview !== null && isJobActive(preview.job?.status)) {
      previewPollTimer.current = window.setInterval(() => {
        void pollPreview();
      }, POLL_INTERVAL_MS);
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

  const onRuleChange = useCallback((next: RuleDraft) => {
    // 修改规则使旧结果失效：由 previewInvalidated 统一判定。
    setRule(next);
  }, []);

  const onStartPreview = useCallback(async () => {
    if (previewCreating) {
      return;
    }
    setPreviewCreating(true);
    setPreviewError('');
    setSelection([]);
    setExecution(null);
    setConfirmText('');
    try {
      const created = await createPreview(rule, storeSummary?.store?.storeId ?? null);
      const payload = await getPreview(created.preview_id);
      setPreview(payload);
    } catch (error) {
      setPreviewError(errorMessage(error));
    } finally {
      setPreviewCreating(false);
    }
  }, [previewCreating, rule, storeSummary]);

  const onExecute = useCallback(async () => {
    if (preview === null || executing) {
      return;
    }
    setExecuting(true);
    setExecutionError('');
    const idempotencyKey = `${preview.preview_id}:${selection.join(',')}:${limit}:${writeFeishu ? '1' : '0'}`;
    try {
      const created = await createExecution({
        preview_id: preview.preview_id,
        apply_ids: selection,
        limit,
        write_feishu: writeFeishu,
        confirmation: confirmText,
        idempotency_key: idempotencyKey,
      });
      setExecution(await getExecution(created.execution_id));
      setConfirmText('');
    } catch (error) {
      setExecutionError(errorMessage(error));
      // 网络异常先查任务状态：若已有执行批次则恢复。
      if (preview !== null) {
        getRecent()
          .then(({executions}) => {
            const match = executions.find(
              (item) => item.preview_id === preview.preview_id,
            );
            if (match !== undefined) {
              return getExecution(match.execution_id);
            }
            return null;
          })
          .then((payload) => {
            if (payload !== null) {
              setExecution(payload);
            }
          })
          .catch(() => {});
      }
    } finally {
      setExecuting(false);
    }
  }, [preview, executing, selection, limit, writeFeishu, confirmText]);

  const onReconcile = useCallback(async () => {
    if (execution === null || reconciling) {
      return;
    }
    setReconciling(true);
    setReconcileError('');
    try {
      const result = await reconcileExecution(execution.execution_id, {
        write_feishu: execution.write_feishu,
        confirmation: reconcileConfirmText,
      });
      void result;
      setExecution(await getExecution(execution.execution_id));
      setReconcileConfirmText('');
    } catch (error) {
      setReconcileError(errorMessage(error));
    } finally {
      setReconciling(false);
    }
  }, [execution, reconciling, reconcileConfirmText]);

  const onCheckEnvironment = useCallback(async () => {
    if (checkingEnvironment) {
      return;
    }
    setCheckingEnvironment(true);
    setEnvironmentNote('');
    try {
      const {job_id} = await createEnvironmentCheck();
      const poll = async () => {
        try {
          const {events} = await getJobEvents(job_id, 0);
          const last = events[events.length - 1];
          if (last !== undefined) {
            setEnvironmentNote(last.message);
          }
        } catch {
          // 检查任务状态轮询失败静默。
        }
      };
      void poll();
      const timer = window.setInterval(() => {
        void poll();
      }, POLL_INTERVAL_MS);
      window.setTimeout(() => window.clearInterval(timer), 30000);
    } catch (error) {
      setEnvironmentNote(`环境检查创建失败：${errorMessage(error)}`);
    } finally {
      setCheckingEnvironment(false);
    }
  }, [checkingEnvironment]);

  return (
    <Stack gap={3} padding={3}>
      <TopNav />
      {optionsError !== '' && (
        <ReadinessBanner message={optionsError} />
      )}
      {previewError !== '' && <PreviewErrorBanner message={previewError} />}
      {previewEvents.length > 0 && preview !== null && isJobActive(preview.job?.status) && (
        <TaskProgress
          label={`筛查任务 ${preview.preview_id}`}
          events={previewEvents}
        />
      )}
      <ReadinessPanel
        storeSummary={storeSummary}
        busy={busy}
        checkingEnvironment={checkingEnvironment}
        onCheckEnvironment={onCheckEnvironment}
        onRefreshStore={refreshStore}
        environmentNote={environmentNote}
      />
      <RuleConfigPanel
        options={options}
        rule={rule}
        displayMode={displayMode}
        onDisplayModeChange={setDisplayMode}
        onCopyToCustom={() => {
          setRule(buildStandardRule(options));
          setDisplayMode('custom');
        }}
        onRuleChange={onRuleChange}
        onStartPreview={() => void onStartPreview()}
        previewRunning={previewCreating || (preview !== null && isJobActive(preview.job?.status))}
        busy={busy}
        previewInvalidated={previewInvalidated}
      />
      {preview !== null && preview.status === 'completed' && (
        <ResultsPanel
          preview={preview}
          selection={selection}
          onSelectionChange={setSelection}
          previewInvalidated={previewInvalidated}
          staleReason="规则或阈值已修改，旧结果已失效。"
        />
      )}
      <ExecutionPanel
        options={options}
        preview={preview !== null && preview.status === 'completed' ? preview : null}
        previewInvalidated={previewInvalidated}
        selection={selection}
        limit={limit}
        onLimitChange={setLimit}
        writeFeishu={writeFeishu}
        onWriteFeishuChange={setWriteFeishu}
        confirmText={confirmText}
        onConfirmTextChange={setConfirmText}
        onExecute={() => void onExecute()}
        executing={executing}
        executionError={executionError}
        execution={execution}
        reconcileConfirmText={reconcileConfirmText}
        onReconcileConfirmTextChange={setReconcileConfirmText}
        onReconcile={() => void onReconcile()}
        reconciling={reconciling}
        reconcileError={reconcileError}
      />
    </Stack>
  );
}

function ReadinessBanner({message}: {message: string}) {
  return <Banner status="warning" title="配置提示" description={message} />;
}

function PreviewErrorBanner({message}: {message: string}) {
  return <Banner status="error" title="筛查创建失败" description={message} />;
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
