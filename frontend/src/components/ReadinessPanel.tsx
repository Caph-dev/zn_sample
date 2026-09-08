import {Banner} from '@astryxdesign/core/Banner';
import {Button} from '@astryxdesign/core/Button';
import {Section} from '@astryxdesign/core/Section';
import {Stack} from '@astryxdesign/core/Stack';
import {StatusDot} from '@astryxdesign/core/StatusDot';

import type {StoreSummary} from '../types';

interface ReadinessPanelProps {
  storeSummary: StoreSummary | null;
  busy: boolean;
  checkingEnvironment: boolean;
  onCheckEnvironment: () => void;
  onRefreshStore: () => void;
  environmentNote: string;
}

export function ReadinessPanel({
  storeSummary,
  busy,
  checkingEnvironment,
  onCheckEnvironment,
  onRefreshStore,
  environmentNote,
}: ReadinessPanelProps) {
  const debugReady = storeSummary?.debug_ready === true;
  const storeName = storeSummary?.store?.storeName ?? '';
  const storeId = storeSummary?.store?.storeId ?? '';
  let statusVariant: 'success' | 'warning' | 'error' | 'neutral' = 'neutral';
  let statusLabel = '未检测';
  if (busy) {
    statusVariant = 'warning';
    statusLabel = '冲突任务运行中（紫鸟通道被占用）';
  } else if (storeSummary === null) {
    statusLabel = '正在读取店铺状态…';
  } else if (!storeSummary.ok) {
    statusVariant = 'error';
    statusLabel = storeSummary.error === 'running-not-unique' ? '运行店铺不唯一' : '店铺状态读取失败';
  } else if (debugReady) {
    statusVariant = 'success';
    statusLabel = '已就绪（调试通道探活通过）';
  } else {
    statusVariant = 'warning';
    statusLabel = '已运行但调试通道未就绪';
  }

  return (
    <Section>
      <Stack gap={2}>
        <Stack direction="horizontal" gap={2} vAlign="center">
          <h2 className="section-title">1 · 运行准备</h2>
          <StatusDot variant={statusVariant} label={statusLabel} tooltip={statusLabel} />
          <span>{statusLabel}</span>
        </Stack>
        <Stack direction="horizontal" gap={3} vAlign="center">
          <span>店铺：{storeName || '—'}</span>
          <span>店铺 ID：{storeId || '—'}</span>
          <Button label="重新检测" size="sm" variant="secondary" onClick={onRefreshStore} />
          <Button
            label="检查环境"
            size="sm"
            variant="secondary"
            isLoading={checkingEnvironment}
            onClick={onCheckEnvironment}
          />
        </Stack>
        {busy && (
          <Banner
            status="warning"
            title="有任务正在占用紫鸟通道"
            description="冲突任务运行期间不会并发探测；环境状态为缓存结果。等任务结束后再开始筛查。"
          />
        )}
        {!debugReady && !busy && storeSummary !== null && (
          <Banner
            status="warning"
            title="调试通道未就绪"
            description="「有店在运行」不等于可执行；请按 SOP 用 0 号入口开店（带调试口）后重试。"
          />
        )}
        {environmentNote !== '' && (
          <Banner status="info" title="环境检查" description={environmentNote} />
        )}
      </Stack>
    </Section>
  );
}
