import {Banner} from '@astryxdesign/core/Banner';
import {Button} from '@astryxdesign/core/Button';
import {Heading} from '@astryxdesign/core/Heading';
import {Section} from '@astryxdesign/core/Section';
import {Stack} from '@astryxdesign/core/Stack';
import {Text} from '@astryxdesign/core/Text';

import {OperatorGroups} from '../components/OperatorGroups';
import {PageHeader} from '../components/PageHeader';
import {PreparationStatus} from '../components/PreparationStatus';
import {describePreparation, usePreparationStatus} from '../preparation';
import type {PrepareData} from '../types';

/** 运行准备：调试口状态 + 检查环境 + 打开店铺。 */
export function PreparePage({data}: {data: PrepareData}) {
  const storeSummary = usePreparationStatus();
  const preparation = describePreparation(storeSummary);

  return (
    <Stack gap={4}>
      <PageHeader
        eyebrow="事前准备"
        title="运行准备"
        description="先让目标店带调试口运行，再执行筛查、批准或跟进；本页只负责店铺准备，不写飞书、不发私信。"
      />

      {preparation.ready && (
        <Banner
          status="success"
          container="card"
          title={<Text size="xl" weight="semibold">当前环境已经可用</Text>}
          description={
            <Text size="lg">
              {`调试口探活通过：${preparation.store}。可以直接执行筛查、批准或跟进任务。`}
            </Text>
          }
        />
      )}

      <Section>
        <Stack gap={3}>
          <Heading level={2}>店铺状态</Heading>
          <PreparationStatus summary={storeSummary} />
          <form method="post" action="/api/jobs/environment-check" data-job-form data-job-label="店铺连接检查">
            <Button type="submit" label="检查环境" variant="secondary" />
          </form>
        </Stack>
      </Section>

      <Banner
        status="info"
        title="两阶段导航"
        description="打开店铺后需在商家中心完成登录，并停在「样品申请 → 待审核」；正式筛查/批准脚本才能继续。"
      />

      <OperatorGroups groups={data.operator_groups} prepareReady={storeSummary?.debug_ready === true} />

      <Text type="supporting">
        运行自动化脚本时，只保留 1 家店铺（2 号店），请勿同时操作店铺页面或者关闭紫鸟浏览器。
      </Text>
    </Stack>
  );
}
