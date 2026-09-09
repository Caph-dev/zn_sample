import {Button} from '@astryxdesign/core/Button';
import {EmptyState} from '@astryxdesign/core/EmptyState';
import {Link} from '@astryxdesign/core/Link';
import {Stack} from '@astryxdesign/core/Stack';
import {Table, pixel, proportional} from '@astryxdesign/core/Table';

import {PageHeader} from '../components/PageHeader';
import {StatusToken} from '../components/StatusToken';
import type {JobRow} from '../types';

export function JobsPage({data}: {data: {rows: JobRow[]}}) {
  return (
    <Stack gap={4}>
      <PageHeader
        eyebrow="后台任务"
        title="任务"
        description="这里记录网页发起的筛查执行、同步、待办生成、检查和报表任务；进度会在页面上方实时显示。"
        actions={
          <form
            method="post"
            action="/api/jobs/environment-check"
            data-job-form
            data-job-label="店铺连接检查"
          >
            <Button type="submit" label="检查店铺连接" variant="secondary" />
          </form>
        }
      />

      {data.rows.length === 0 ? (
        <EmptyState title="暂无任务" description="在首页或报表页发起一次任务后，这里会显示历史记录。" />
      ) : (
        <Table
          data={data.rows as unknown as Record<string, unknown>[]}
          idKey="id"
          density="compact"
          hasHover
          columns={[
            {
              key: 'job_label',
              header: '任务',
              width: proportional(3),
              renderCell: (row) => {
                const job = row as unknown as JobRow;
                return <Link href={`/jobs/${job.id}`}>{job.job_label}</Link>;
              },
            },
            {
              key: 'status',
              header: '状态',
              width: pixel(140),
              renderCell: (row) => {
                const job = row as unknown as JobRow;
                return <StatusToken label={job.status_label} tone={job.status_tone} />;
              },
            },
            {
              key: 'created_at',
              header: '创建时间',
              width: proportional(2),
            },
          ]}
        />
      )}
    </Stack>
  );
}
