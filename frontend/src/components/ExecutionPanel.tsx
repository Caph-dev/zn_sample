import {AlertDialog} from '@astryxdesign/core/AlertDialog';
import {Banner} from '@astryxdesign/core/Banner';
import {Button} from '@astryxdesign/core/Button';
import {NumberInput} from '@astryxdesign/core/NumberInput';
import {Section} from '@astryxdesign/core/Section';
import {Stack} from '@astryxdesign/core/Stack';
import {StatusDot} from '@astryxdesign/core/StatusDot';
import {Switch} from '@astryxdesign/core/Switch';
import {TextInput} from '@astryxdesign/core/TextInput';
import {useMemo, useState} from 'react';

import type {ExecutionPayload, OptionsPayload, PreviewPayload} from '../types';

interface ExecutionPanelProps {
  options: OptionsPayload | null;
  preview: PreviewPayload | null;
  previewInvalidated: boolean;
  selection: string[];
  limit: number;
  onLimitChange: (limit: number) => void;
  writeFeishu: boolean;
  onWriteFeishuChange: (checked: boolean) => void;
  confirmText: string;
  onConfirmTextChange: (value: string) => void;
  onExecute: () => void;
  executing: boolean;
  executionError: string;
  execution: ExecutionPayload | null;
  reconcileConfirmText: string;
  onReconcileConfirmTextChange: (value: string) => void;
  onReconcile: () => void;
  reconciling: boolean;
  reconcileError: string;
}

const APPROVE_STATUS_LABELS: Record<string, string> = {
  queued: '等待执行',
  approved: '平台批准成功',
  failed: '批准失败',
  unknown: '结果未知（待核对）',
  skipped: '已跳过',
  error: '异常',
};

const FEISHU_STATUS_LABELS: Record<string, string> = {
  created: '已新建',
  'not-requested': '未请求',
  'write-uncertain': '写后待核对',
  error: '写失败',
  'ambiguous-match': '去重歧义',
  'unique-match': '已存在（跳过）',
  'invalid-match': '记录无效',
  '': '—',
};

function statusVariant(status: string): 'success' | 'error' | 'warning' | 'neutral' {
  if (status === 'approved' || status === 'created') {
    return 'success';
  }
  if (status === 'failed' || status === 'error') {
    return 'error';
  }
  if (status === 'unknown' || status === 'write-uncertain' || status === 'ambiguous-match') {
    return 'warning';
  }
  return 'neutral';
}

export function ExecutionPanel({
  options,
  preview,
  previewInvalidated,
  selection,
  limit,
  onLimitChange,
  writeFeishu,
  onWriteFeishuChange,
  confirmText,
  onConfirmTextChange,
  onExecute,
  executing,
  executionError,
  execution,
  reconcileConfirmText,
  onReconcileConfirmTextChange,
  onReconcile,
  reconciling,
  reconcileError,
}: ExecutionPanelProps) {
  const [dialogOpen, setDialogOpen] = useState(false);
  const limitMax = options?.limits.execute_limit_max ?? 10;
  const canExecute = useMemo(() => {
    if (preview === null || previewInvalidated) {
      return false;
    }
    if (preview.status !== 'completed' || !preview.integrity_complete || !preview.is_fresh) {
      return false;
    }
    if (selection.length === 0 || selection.length > limit) {
      return false;
    }
    return confirmText.trim().toLowerCase() === 'y';
  }, [preview, previewInvalidated, selection, limit, confirmText]);

  if (preview === null) {
    return (
      <Section>
        <Stack gap={2}>
          <h2 className="section-title">4 · 执行确认</h2>
          <p className="hint-text">先完成「开始只读筛查」并选择可批准名单，才能进入执行确认。</p>
        </Stack>
      </Section>
    );
  }

  const selectedNames = preview.rows
    .filter((row) => selection.includes(row.apply_id))
    .map((row) => `${row.creator_name}（${row.apply_id}）`);

  const feishuTarget =
    preview.rule.content.enabled || !writeFeishu
      ? '达人关系管理(新) / 达人管理总表'
      : '达人关系管理(新) / 达人管理总表';

  return (
    <Section>
      <Stack gap={3}>
        <h2 className="section-title">4 · 执行确认</h2>

        <Stack gap={1}>
          <h3 className="subsection-title">本次规则（服务器快照）</h3>
          {preview.rule_summary.map((line) => (
            <p key={line} className="summary-line">
              {line}
            </p>
          ))}
        </Stack>

        <Stack direction="horizontal" gap={3} vAlign="center">
          <NumberInput
            label="本次限量（最多批准 N 条）"
            value={limit}
            onChange={onLimitChange}
            min={1}
            max={limitMax}
            description={`上限 ${limitMax}；0/负值不会变成不限`}
            isDisabled={execution !== null}
          />
          <Switch
            label="批准后写入飞书（达人关系管理(新)）"
            value={writeFeishu}
            onChange={onWriteFeishuChange}
            description="写表目标：达人关系管理(新) / 达人管理总表；默认关闭"
            isDisabled={execution !== null}
          />
        </Stack>

        <Stack gap={1}>
          <h3 className="subsection-title">已选可批准名单（按此顺序执行）</h3>
          {selectedNames.length === 0 ? (
            <p className="hint-text">未选择任何行；空名单不可执行。</p>
          ) : (
            <ol className="selection-list">
              {selectedNames.map((name, index) => (
                <li key={name}>
                  {index + 1}. {name}
                </li>
              ))}
            </ol>
          )}
        </Stack>

        {previewInvalidated && (
          <Banner
            status="error"
            title="执行被拦截"
            description="规则已修改或结果失效，请重新筛查后再执行。"
          />
        )}
        {!preview.is_fresh && preview.status === 'completed' && (
          <Banner
            status="error"
            title="结果已过期"
            description="筛查结果超过新鲜度时限，必须重新筛查后才能执行。"
          />
        )}
        {executionError !== '' && (
          <Banner status="error" title="执行创建失败" description={executionError} />
        )}

        <Stack direction="horizontal" gap={2} vAlign="end">
          <TextInput
            label="输入 y 确认（明确确认后才能点执行）"
            value={confirmText}
            onChange={onConfirmTextChange}
            placeholder="y"
            isDisabled={execution !== null}
          />
          <Button
            label="执行限量批准"
            variant="destructive"
            isDisabled={!canExecute}
            isLoading={executing}
            onClick={() => setDialogOpen(true)}
          />
        </Stack>
        <p className="hint-text">
          本次按自定义标准执行，不代表完整 SOP 通过；平台「同意」不可自动撤销；
          批准成功后才会写飞书；不发送任何私信。
        </p>

        <AlertDialog
          isOpen={dialogOpen}
          onOpenChange={setDialogOpen}
          title="确认执行限量批准"
          description={
            `店铺：${preview.store_id}；限量 ${limit} 条；写飞书：${writeFeishu ? '开（' + feishuTarget + '）' : '关'}。` +
            `名单：${selectedNames.join('、') || '无'}。` +
            '本次按自定义标准，不代表完整 SOP 通过。平台同意不可撤销，批准前已生成本地备份。'
          }
          actionLabel="确认批准"
          cancelLabel="取消"
          onAction={() => {
            setDialogOpen(false);
            onExecute();
          }}
        />

        {execution !== null && (
          <Stack gap={2}>
            <h3 className="subsection-title">
              执行批次 {execution.execution_id} · 状态 {execution.status}
            </h3>
            {execution.error_summary !== '' && (
              <Banner status="error" title="执行批次失败" description={execution.error_summary} />
            )}
            <Stack gap={1}>
              {execution.items.map((item) => {
                const approveLabel =
                  APPROVE_STATUS_LABELS[item.approve_status] ?? item.approve_status;
                const feishuLabel =
                  FEISHU_STATUS_LABELS[item.feishu_relation_status] ??
                  item.feishu_relation_status;
                return (
                  <Stack key={item.apply_id} gap={1}>
                    <Stack direction="horizontal" gap={2} vAlign="center">
                      <StatusDot
                        variant={statusVariant(item.approve_status)}
                        label={approveLabel}
                      />
                      <span>
                        {item.creator_name}（{item.apply_id}）平台：{approveLabel}
                      </span>
                      <StatusDot
                        variant={statusVariant(item.feishu_relation_status)}
                        label={feishuLabel}
                      />
                      <span>飞书：{feishuLabel}</span>
                    </Stack>
                    {item.approve_error !== '' && (
                      <p className="hint-text">批准说明：{item.approve_error}</p>
                    )}
                    {item.feishu_error !== '' && (
                      <p className="hint-text">飞书说明：{item.feishu_error}</p>
                    )}
                  </Stack>
                );
              })}
            </Stack>

            {execution.status === 'completed' && (
              <Banner
                status="info"
                title="执行完成"
                description="平台列表约 10 分钟后刷新；可用「核对补写」延后确认待发货、补飞书并回填订单号（不重新批准）。"
              />
            )}
            {reconcileError !== '' && (
              <Banner status="error" title="核对创建失败" description={reconcileError} />
            )}
            {execution.status === 'completed' || execution.status === 'needs_reconcile' ? (
              <Stack direction="horizontal" gap={2} vAlign="end">
                <TextInput
                  label="输入 y 确认核对补写（写飞书）"
                  value={reconcileConfirmText}
                  onChange={onReconcileConfirmTextChange}
                  placeholder="y"
                />
                <Button
                  label="核对补写"
                  variant="secondary"
                  isLoading={reconciling}
                  isDisabled={reconcileConfirmText.trim().toLowerCase() !== 'y'}
                  onClick={onReconcile}
                />
              </Stack>
            ) : null}
          </Stack>
        )}
      </Stack>
    </Section>
  );
}
