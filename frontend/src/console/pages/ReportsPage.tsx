import {Banner} from '@astryxdesign/core/Banner';
import {Button} from '@astryxdesign/core/Button';
import {Stack} from '@astryxdesign/core/Stack';

import {PageHeader} from '../components/PageHeader';
import type {ReportsData} from '../types';

export function ReportsPage({data}: {data: ReportsData}) {
  return (
    <Stack gap={4}>
      <PageHeader
        eyebrow="导出"
        title="CSV 报表"
        description="报表写入本机用户数据目录 exports/。"
      />
      <Banner
        status="info"
        title="本地文件"
        description="生成后在任务面板的「运行结果」里点「下载 CSV」，不会写飞书、不发私信。"
      />
      <Stack direction="horizontal" gap={2} wrap="wrap">
        {data.report_kinds.map((kind) => (
          <form
            key={kind.value}
            method="post"
            action="/api/jobs/report-export"
            data-job-form
            data-job-label={`${kind.label}报表`}
          >
            <input type="hidden" name="kind" value={kind.value} />
            <Button type="submit" label={kind.label} variant="secondary" />
          </form>
        ))}
      </Stack>
    </Stack>
  );
}
