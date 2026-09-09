import {Banner} from '@astryxdesign/core/Banner';
import {Button} from '@astryxdesign/core/Button';
import {Heading} from '@astryxdesign/core/Heading';
import {Section} from '@astryxdesign/core/Section';
import {Stack} from '@astryxdesign/core/Stack';
import {StatusDot} from '@astryxdesign/core/StatusDot';
import {Text} from '@astryxdesign/core/Text';
import {Token} from '@astryxdesign/core/Token';

import type {StoreSummary} from '../types';

interface ReadinessPanelProps {
  storeSummary: StoreSummary | null;
  busy: boolean;
  prepareHref: string;
}

/**
 * 运行准备降级为只读状态条：探活与「打开店铺」统一由 /prepare 负责，
 * 本页不再单独探测，避免两处缓存不一致。
 */
export function ReadinessPanel({storeSummary, busy, prepareHref}: ReadinessPanelProps) {
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
    statusLabel = '已就绪';
  } else {
    statusVariant = 'warning';
    statusLabel = '已运行但调试通道未就绪';
  }
  const isReady = statusVariant === 'success';

  return (
    <Section>
      <Stack gap={2}>
        <Stack direction="horizontal" gap={2} vAlign="center">
          <Heading level={2}>1 · 运行准备</Heading>
          {isReady ? (
            <>
              <Token color="green" label={statusLabel} className="readiness-ready-token" />
              <Text type="supporting">以运行准备页探活结果为准</Text>
            </>
          ) : (
            <>
              <StatusDot variant={statusVariant} label={statusLabel} tooltip={statusLabel} />
              <Text>{statusLabel}</Text>
              <Button label="去运行准备" href={prepareHref} size="sm" variant="secondary" />
            </>
          )}
        </Stack>
        <Stack direction="horizontal" gap={3} vAlign="center">
          <Text>店铺：{storeName || '—'}</Text>
          <Text>店铺 ID：{storeId || '—'}</Text>
        </Stack>
        {busy && (
          <Banner
            status="warning"
            title="有任务正在占用紫鸟通道"
            description="冲突任务运行期间不并发探测；环境状态为缓存结果。等任务结束后再开始筛查。"
          />
        )}
        {!debugReady && !busy && storeSummary !== null && (
          <Banner
            status="warning"
            title="调试通道未就绪"
            description="「有店在运行」不等于可执行；请到「运行准备」页用 0 号入口开店（带调试口）后重试。"
          />
        )}
      </Stack>
    </Section>
  );
}
