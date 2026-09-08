/** 本页 API 客户端；所有写操作都经服务端严格校验。 */

import type {
  ExecutionPayload,
  OptionsPayload,
  PreviewPayload,
  RuleDraft,
  StoreSummary,
} from './types';

export interface ApiError {
  code: string;
  message: string;
  status: number;
}

export async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: 'same-origin',
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    let detail: unknown = null;
    try {
      detail = await response.json();
    } catch {
      detail = null;
    }
    let code = 'http-error';
    let message = `请求失败（${response.status}）`;
    if (detail && typeof detail === 'object') {
      const objectDetail = detail as {code?: unknown; message?: unknown; detail?: unknown};
      if (typeof objectDetail.code === 'string') {
        code = objectDetail.code;
      }
      if (typeof objectDetail.message === 'string') {
        message = objectDetail.message;
      } else if (typeof objectDetail.detail === 'string') {
        message = objectDetail.detail;
      }
    }
    throw {code, message, status: response.status} satisfies ApiError;
  }
  return (await response.json()) as T;
}

export function getOptions(): Promise<OptionsPayload> {
  return fetchJson<OptionsPayload>('/api/auto-approval/options');
}

export function getStorePreparation(): Promise<StoreSummary> {
  return fetchJson<StoreSummary>('/api/stores/preparation-status');
}

export function createPreview(rule: RuleDraft, storeId: string | null): Promise<{preview_id: string; job_id: string}> {
  return fetchJson<{preview_id: string; job_id: string}>('/api/auto-approval/previews', {
    method: 'POST',
    body: JSON.stringify({rule, store_id: storeId}),
  });
}

export function getPreview(previewId: string): Promise<PreviewPayload> {
  return fetchJson<PreviewPayload>(`/api/auto-approval/previews/${previewId}`);
}

export function createExecution(payload: {
  preview_id: string;
  apply_ids: string[];
  limit: number;
  write_feishu: boolean;
  confirmation: string;
  idempotency_key: string;
}): Promise<{execution_id: string; job_id: string; deduplicated: boolean; status: string}> {
  return fetchJson<{execution_id: string; job_id: string; deduplicated: boolean; status: string}>(
    '/api/auto-approval/executions',
    {method: 'POST', body: JSON.stringify(payload)},
  );
}

export function getExecution(executionId: string): Promise<ExecutionPayload> {
  return fetchJson<ExecutionPayload>(`/api/auto-approval/executions/${executionId}`);
}

export function reconcileExecution(
  executionId: string,
  payload: {write_feishu: boolean; confirmation: string},
): Promise<{execution_id: string; job_id: string}> {
  return fetchJson<{execution_id: string; job_id: string}>(
    `/api/auto-approval/executions/${executionId}/reconcile`,
    {method: 'POST', body: JSON.stringify(payload)},
  );
}

export function createEnvironmentCheck(): Promise<{job_id: string; deduplicated: boolean}> {
  return fetchJson<{job_id: string; deduplicated: boolean}>('/api/jobs/environment-check', {
    method: 'POST',
    body: '{}',
  });
}

export interface RecentPayload {
  previews: {
    preview_id: string;
    store_id: string;
    status: string;
    rule_hash: string;
    integrity_complete: boolean;
    created_at: string;
    finished_at: string | null;
  }[];
  executions: {
    execution_id: string;
    preview_id: string;
    status: string;
    write_feishu: boolean;
    created_at: string;
    finished_at: string | null;
  }[];
}

export function getRecent(): Promise<RecentPayload> {
  return fetchJson<RecentPayload>('/api/auto-approval/recent');
}

export interface JobEvent {
  sequence: number;
  level: string;
  event_type: string;
  message: string;
  created_at: string;
}

export function getJobEvents(jobId: string, after: number): Promise<{events: JobEvent[]}> {
  return fetchJson<{events: JobEvent[]}>(`/api/jobs/${jobId}/events?after=${after}`);
}
