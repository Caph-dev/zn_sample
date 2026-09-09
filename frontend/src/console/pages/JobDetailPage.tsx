import {Heading} from '@astryxdesign/core/Heading';
import {Section} from '@astryxdesign/core/Section';
import {Stack} from '@astryxdesign/core/Stack';
import {Text} from '@astryxdesign/core/Text';
import {useEffect} from 'react';

import {PageHeader} from '../components/PageHeader';
import {StatusToken} from '../components/StatusToken';
import type {JobDetailData} from '../types';

export function JobDetailPage({data}: {data: JobDetailData}) {
  // React 挂载后再通知 app.js 开始监控，避免与 DOMContentLoaded 抢时序。
  useEffect(() => {
    document.dispatchEvent(
      new CustomEvent('assistant:monitor-job', {detail: {jobId: data.id}}),
    );
  }, [data.id]);

  return (
    <Stack gap={5}>
      <PageHeader
        eyebrow="后台任务"
        title={data.job_label}
        description={`创建时间 ${data.created_at}`}
      />

      <Section>
        <Stack gap={2}>
          <Heading level={2}>任务状态</Heading>
          <Stack
            gap={2}
            data-job-monitor
            data-job-id={data.id}
            data-global-task-panel="false"
          />
          <Stack direction="horizontal" gap={2} vAlign="center">
            <StatusToken label={data.status_label} tone={data.status_tone} />
            <Text type="supporting">
              {data.progress_total > 0
                ? `${data.progress_current} / ${data.progress_total}`
                : '等待任务开始'}
            </Text>
          </Stack>
          <Text type="supporting">{data.progress_message || '等待任务开始'}</Text>
        </Stack>
      </Section>

      <Section>
        <Stack gap={2}>
          <Heading level={2}>执行过程</Heading>
          <Stack as="ol" gap={1} data-job-events aria-label="任务执行过程" />
        </Stack>
      </Section>

      <Section>
        <Stack gap={2} data-job-result aria-live="polite" />
      </Section>
    </Stack>
  );
}
