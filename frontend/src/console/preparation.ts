import {useEffect, useRef, useState} from 'react';

import type {StoreSummary} from './types';

/** 调试口状态由页面直接轮询 `/api/stores/preparation-status`，不依赖 app.js。 */
const PREPARATION_POLL_INTERVAL_MS = 10000;

export interface PreparationView {
  summary: string;
  store: string;
  debug: string;
  hint: string;
  tone: 'success' | 'warning' | 'accent';
  ready: boolean;
}

export function describeStore(summary: StoreSummary | null): string {
  if (summary === null) {
    return '正在检测';
  }
  if (summary.ok === true && summary.store !== undefined) {
    return `${summary.store.storeName}（${summary.store.storeId}）`;
  }
  if (summary.error === 'running-query-failed') {
    return '暂时无法读取当前店铺';
  }
  const runningStores = summary.stores ?? [];
  if (runningStores.length === 0) {
    return '当前没有打开的店铺';
  }
  if (runningStores.length > 1) {
    return `当前打开了 ${runningStores.length} 家店铺，请只保留一家`;
  }
  const store = runningStores[0];
  return `${store.storeName}（${store.storeId}）`;
}

export function describePreparation(summary: StoreSummary | null): PreparationView {
  const store = describeStore(summary);
  if (summary !== null && summary.error === 'ziniao-busy') {
    return {
      summary: '店铺任务运行中',
      store,
      debug: '任务结束后自动重新检测',
      hint: '当前不会并发探测调试口，避免干扰正在运行的任务。',
      tone: 'accent',
      ready: false,
    };
  }
  const ready = summary?.ok === true && summary.debug_ready === true;
  if (ready) {
    return {
      summary: '店铺已准备好',
      store,
      debug: '已打开，可以进入下一步',
      hint: '当前已满足自动化脚本的运行要求；再次点击「打开店铺」可以重新打开店铺。',
      tone: 'success',
      ready: true,
    };
  }
  return {
    summary: '店铺尚未准备好',
    store,
    debug: '未打开或尚未就绪',
    hint:
      summary?.error === 'running-query-failed'
        ? '暂时无法读取紫鸟状态，请检查紫鸟 GUI 和 Bridge。'
        : '请点击「打开店铺」，等待调试口就绪后再进入下一步。',
    tone: 'warning',
    ready: false,
  };
}

export function usePreparationStatus(): StoreSummary | null {
  const [summary, setSummary] = useState<StoreSummary | null>(null);
  const requestInFlight = useRef(false);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;

    const load = async () => {
      if (requestInFlight.current) {
        return;
      }
      requestInFlight.current = true;
      try {
        const response = await fetch('/api/stores/preparation-status', {
          credentials: 'same-origin',
          headers: {Accept: 'application/json'},
        });
        if (!response.ok) {
          throw new Error(`preparation-status ${response.status}`);
        }
        const payload = (await response.json()) as StoreSummary;
        if (!cancelled) {
          setSummary(payload);
        }
      } catch {
        if (!cancelled) {
          setSummary({ok: false, error: 'running-query-failed'});
        }
      } finally {
        requestInFlight.current = false;
        if (!cancelled) {
          timer = window.setTimeout(() => {
            void load();
          }, PREPARATION_POLL_INTERVAL_MS);
        }
      }
    };

    void load();

    const onJobComplete = (event: Event) => {
      const detail = (event as CustomEvent<{job_type?: string}>).detail;
      if (detail?.job_type === 'operator_prepare') {
        window.setTimeout(() => void load(), 500);
      }
    };
    document.addEventListener('assistant:job-complete', onJobComplete);
    return () => {
      cancelled = true;
      if (timer !== undefined) {
        window.clearTimeout(timer);
      }
      document.removeEventListener('assistant:job-complete', onJobComplete);
    };
  }, []);

  return summary;
}
