import type {ExecutionRecord} from './types';

export type ApprovalProgress = 'pending' | 'processing' | 'approved' | 'needs_review';

export function getApprovalProgress(records: ExecutionRecord[]): Map<string, ApprovalProgress> {
  const progress = new Map<string, ApprovalProgress>();
  for (const record of records) {
    for (const item of record.items) {
      const current = progress.get(item.apply_id);
      if (item.approve_status === 'approved') {
        progress.set(item.apply_id, 'approved');
      } else if (item.approve_status === 'unknown' && current !== 'approved') {
        progress.set(item.apply_id, 'needs_review');
      } else if (
        item.approve_status === 'queued'
        && ['failed', 'cancelled', 'interrupted'].includes(record.status)
        && current !== 'approved'
      ) {
        progress.set(item.apply_id, 'needs_review');
      } else if (
        (record.status === 'queued' || record.status === 'running')
        && current !== 'approved' && current !== 'needs_review'
      ) {
        progress.set(item.apply_id, 'processing');
      }
    }
  }
  return progress;
}
