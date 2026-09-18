import type {ExecutionPayload} from './types';

export function approvalActivity(execution: ExecutionPayload | null): {
  active: boolean;
  stateError: string;
} {
  const active = execution?.reconciliation?.approval_active ??
    (execution?.job?.status === 'pending' || execution?.job?.status === 'running');
  const stateError = execution?.reconciliation?.state_error ||
    (execution?.status === 'queued' && !active
      ? '历史批次状态异常：批次仍为 queued，但关联任务未在运行。请恢复原批准任务关联，不要重新批准。'
      : '');
  return {active, stateError};
}
