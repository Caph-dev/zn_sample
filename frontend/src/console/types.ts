/** 操作台页面 bootstrap 契约；数据由 assistant/web/console_pages.py 生成。 */

export type StatusTone = 'success' | 'warning' | 'error' | 'accent' | 'neutral';

export type PageKey =
  | 'overview'
  | 'prepare'
  | 'shipments'
  | 'shipment_detail'
  | 'followups'
  | 'followup_detail'
  | 'jobs'
  | 'job_detail'
  | 'reports'
  | 'diagnostics';

export interface ConsoleBootstrap {
  page: PageKey;
  data: unknown;
}

export interface StoreSummary {
  ok?: boolean;
  error?: string;
  store?: {storeId: string; storeName: string};
  stores?: {storeId: string; storeName: string}[];
  debug_ready?: boolean;
  debug_status?: string;
}

export interface QueueItem {
  label: string;
  href: string;
  count: number;
  tone: StatusTone;
}

export interface OperatorConfirm {
  token: string;
  title: string;
  description: string;
  action: string;
}

export interface OperatorItem {
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
  confirm: OperatorConfirm | null;
  force_gate?: string;
}

export interface OperatorGroup {
  key: string;
  heading: string;
  note: string;
  items: OperatorItem[];
}

export interface OverviewData {
  data_directory: string;
  database_ready: boolean;
  store_summary: StoreSummary;
  queue_items: QueueItem[];
}

export interface PrepareData {
  data_directory: string;
  database_ready: boolean;
  store_summary: StoreSummary;
  operator_groups: OperatorGroup[];
}

export interface DiagnosticsData {
  python_version: string;
  cli_state: string;
  bridge_state: string;
  config_exists: boolean;
  feishu_configured: boolean;
  stores: {storeId: string; storeName: string}[];
  store_error: string;
}

export interface ReportKind {
  value: string;
  label: string;
}

export interface ReportsData {
  report_kinds: ReportKind[];
}

export interface JobRow {
  id: string;
  job_type: string;
  job_label: string;
  status: string;
  status_label: string;
  status_tone: StatusTone;
  created_at: string;
}

export interface JobDetailData extends JobRow {
  progress_current: number;
  progress_total: number;
  progress_message: string;
  error_summary: string;
  error_code: string;
  is_operator_job: boolean;
  is_active: boolean;
}

export interface ShipmentRow {
  id: number;
  creator_name: string;
  main_order_id: string;
  tracking_display: string;
  status_category: string;
  status_label: string;
  status_tone: StatusTone;
  estimated_delivery_at: string;
  delivered_at: string;
  needs_delivery_confirmation: boolean;
}

export interface ShipmentDetailData extends ShipmentRow {
  creator_id: string;
}

export interface ShipmentsData {
  rows: ShipmentRow[];
  status_filter: string;
  quick_filters: {value: string; label: string}[];
}

export interface FollowupRow {
  id: number;
  creator_name: string;
  stage: string;
  stage_label: string;
  stage_tone: StatusTone;
  action_kind: string;
  action_label: string;
  action_tone: StatusTone;
  action_completed: boolean;
  status: string;
  status_label: string;
  status_tone: StatusTone;
  status_tooltip: string;
  language_label: string;
  platform_status_label: string;
}

export interface FollowupDetailData extends FollowupRow {
  product_id: string;
  main_order_id: string;
  tracking_display: string;
  delivered_at: string;
  scheduled_label: string;
  platform_status_text: string;
  creator_type_label: string;
  message_preview: string;
  attachment_url: string;
  note: string;
  review_label: string;
  send_result: string;
  send_result_label: string;
  send_ready: boolean;
  can_send: boolean;
  can_list: boolean;
  is_unfulfilled_stage: boolean;
}

export interface FollowupFilterOption {
  value: string;
  label: string;
}

export interface FollowupsData {
  rows: FollowupRow[];
  operator_groups: OperatorGroup[];
  filters: {
    stage: string;
    status: string;
    language: string;
    platform_status: string;
    curr_status: number | null;
    include_superseded: boolean;
  };
  filter_options: {
    stages: FollowupFilterOption[];
    statuses: FollowupFilterOption[];
    languages: FollowupFilterOption[];
    platform_statuses: FollowupFilterOption[];
  };
}
