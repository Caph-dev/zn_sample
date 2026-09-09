import {Badge} from '@astryxdesign/core/Badge';
import {Button} from '@astryxdesign/core/Button';
import {Collapsible} from '@astryxdesign/core/Collapsible';
import {Heading} from '@astryxdesign/core/Heading';
import {Item} from '@astryxdesign/core/Item';
import {List} from '@astryxdesign/core/List';
import {MetadataList, MetadataListItem} from '@astryxdesign/core/MetadataList';
import {Section} from '@astryxdesign/core/Section';
import {Stack} from '@astryxdesign/core/Stack';
import {Text} from '@astryxdesign/core/Text';

import {PageHeader} from '../components/PageHeader';
import {PreparationStatus} from '../components/PreparationStatus';
import {describeStore, usePreparationStatus} from '../preparation';
import type {OverviewData} from '../types';

/** 总览：只读计数 + 准备状态摘要，不放任何操作入口。 */
export function OverviewPage({data}: {data: OverviewData}) {
  const storeSummary = usePreparationStatus();

  return (
    <Stack gap={5}>
      <PageHeader
        eyebrow="今日工作台"
        title="总览"
        description="只读汇总今日待处理数量和店铺准备状态；具体操作请到「运行准备」「自动批准」「达人跟进」。"
        actions={<Button label="去运行准备" href="/prepare" variant="secondary" />}
      />

      <Collapsible trigger={<Text type="supporting">运行环境</Text>} defaultIsOpen={false}>
        <MetadataList label={{position: 'start'}}>
          <MetadataListItem label="数据目录">{data.data_directory}</MetadataListItem>
          <MetadataListItem label="数据库">
            {data.database_ready ? '已打开' : '待初始化'}
          </MetadataListItem>
          <MetadataListItem label="运行店铺">{describeStore(data.store_summary)}</MetadataListItem>
        </MetadataList>
      </Collapsible>

      <Section>
        <Stack gap={3}>
          <Heading level={2}>运行准备</Heading>
          <PreparationStatus summary={storeSummary} />
        </Stack>
      </Section>

      <Section>
        <Stack gap={2}>
          <Heading level={2}>今日待处理</Heading>
          <List hasDividers>
            {data.queue_items.map((queueItem) => (
              <Item
                key={queueItem.label}
                as="li"
                label={queueItem.label}
                href={queueItem.href}
                endContent={
                  queueItem.tone === 'neutral' ? (
                    <Badge label={String(queueItem.count)} />
                  ) : (
                    <Badge
                      label={String(queueItem.count)}
                      variant={queueItem.tone === 'error' ? 'error' : 'warning'}
                    />
                  )
                }
              />
            ))}
          </List>
        </Stack>
      </Section>
    </Stack>
  );
}
