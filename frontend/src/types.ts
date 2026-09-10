/** 与后端 rules schema / API 载荷对齐的类型。 */

export type BasicKey =
  | 'followers'
  | 'gmv'
  | 'units'
  | 'gpm'
  | 'aov'
  | 'fulfillment'
  | 'female_pct'
  | 'categories';

export interface BasicDraft {
  enabled: boolean;
  min?: number | null;
  max?: number | null;
  values: string[];
}

export interface SideDraft {
  enabled: boolean;
  gpm?: number | null;
  avg_views?: number | null;
  engagement?: number | null;
}

export interface ContentDraft {
  enabled: boolean;
  days: number;
  min_related: number;
  require_display: boolean;
}

export interface RuleDraft {
  schema_version: 1;
  mode: 'custom';
  product_ids: string[];
  basic: Record<BasicKey, BasicDraft>;
  video_live: {
    enabled: boolean;
    logic: 'either' | 'both';
    video: SideDraft;
    live: SideDraft;
  };
  content: ContentDraft;
}

export interface HeroProduct {
  product_id: string;
  sku: string;
}

export interface OptionsPayload {
  schema_version: number;
  mode: 'custom';
  basic: Record<
    string,
    {
      min: number;
      max: number;
      integer?: boolean;
      default?: number;
      default_min?: number;
      default_max?: number;
    }
  >;
  categories: string[];
  video_live: Record<string, {min: number; max: number; default?: number}>;
  content: {days: number[]; min_related: number[]};
  limits: {
    execute_limit_default: number;
    preview_freshness_seconds: number;
  };
  hero: {ok: boolean; products: HeroProduct[]; error?: string; detail?: string};
  /** 上次保存的自定义规则草稿（仅用于回填，不代表本次已执行）。 */
  saved_rule: {rule: RuleDraft; saved_at: string} | null;
}

export type CheckStatus = 'passed' | 'failed' | 'needs_review' | 'not_checked';

export interface CandidateCheck {
  key: string;
  label: string;
  status: CheckStatus;
  value: number | string | null;
  source: string;
  detail: string;
}

export interface CandidateRow {
  apply_id: string;
  creator_id: string;
  creator_name: string;
  product_id: string;
  overall: 'passed' | 'failed' | 'needs_review';
  content_verdict: 'passed' | 'failed' | 'needs_review' | 'not_checked' | 'not_run';
  custom_eligible: boolean;
  blocked: boolean;
  checks: CandidateCheck[];
  safety_blocks: {code: string; detail: string}[];
  metrics: Record<string, unknown>;
  content_status: string;
  content_reason: string;
  content_evidence_path: string;
  content_related_count: number;
  content_complete: boolean;
  observed_at: string;
}

export interface PreviewPayload {
  preview_id: string;
  store_id: string;
  status: 'queued' | 'completed' | 'failed' | 'cancelled';
  job_id: string;
  rule_hash: string;
  rule: RuleDraft;
  rule_summary: string[];
  integrity_complete: boolean;
  integrity_notes: string[];
  stats: {
    rows: number;
    passed: number;
    failed: number;
    needs_review: number;
    blocked: number;
    eligible: number;
  };
  error_code: string;
  error_summary: string;
  created_at: string;
  finished_at: string | null;
  freshness_seconds: number;
  is_fresh: boolean;
  rows: CandidateRow[];
  job?: JobPayload;
}

export interface JobPayload {
  job_id: string;
  status: string;
  progress_current: number;
  progress_total: number;
  progress_message: string;
  error_code: string;
  error_summary: string;
  log_path: string;
}

export interface ExecutionItem {
  apply_id: string;
  creator_id: string;
  creator_name: string;
  product_id: string;
  approve_status: string;
  approve_error: string;
  action: string;
  platform_confirmation_status: string;
  feishu_relation_status: string;
  feishu_record_id: string;
  feishu_error: string;
  approved_at: string;
}

export interface ExecutionPayload {
  execution_id: string;
  preview_id: string;
  job_id: string;
  status: string;
  store_id: string;
  rule_hash: string;
  apply_ids: string[];
  limit: number;
  write_feishu: boolean;
  error_code: string;
  error_summary: string;
  created_at: string;
  finished_at: string | null;
  items: ExecutionItem[];
  job?: JobPayload;
}

export interface StoreSummary {
  ok: boolean;
  error?: string;
  detail?: string;
  store?: {storeId: string; storeName: string};
  stores?: {storeId: string; storeName: string}[];
  debug_ready?: boolean;
  debug_status?: string;
}

export interface OperatorConfirmPayload {
  token: string;
  title: string;
  description: string;
  action: string;
}

export interface OperatorItemPayload {
  title: string;
  description: string;
  action: string;
  job_label: string;
  button_label: string;
  variant: 'primary' | 'secondary';
  index: string;
  kind: string;
  is_prepare: boolean;
  href: string;
  confirm: OperatorConfirmPayload | null;
  force_gate?: string;
}

export interface OperatorGroupPayload {
  key: string;
  heading: string;
  note: string;
  items: OperatorItemPayload[];
}

/** /auto-approval bootstrap：标准 SOP 只读名单入口 + 运行准备页链接。 */
export interface AutoApprovalBootstrap {
  screen_group: OperatorGroupPayload | null;
  prepare_href: string;
}
