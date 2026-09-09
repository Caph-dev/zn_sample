import {MetadataList, MetadataListItem} from '@astryxdesign/core/MetadataList';
import {Stack} from '@astryxdesign/core/Stack';
import {StatusDot} from '@astryxdesign/core/StatusDot';
import {Text} from '@astryxdesign/core/Text';

import {describePreparation, type PreparationView} from '../preparation';
import type {StoreSummary} from '../types';

/** 准备状态摘要：总览页与运行准备页共用，数据由调用方轮询后传入。 */
export function PreparationStatus({summary}: {summary: StoreSummary | null}) {
  const preparation: PreparationView = describePreparation(summary);
  return (
    <Stack gap={2}>
      <Stack direction="horizontal" gap={2} vAlign="center">
        <StatusDot
          variant={preparation.tone}
          label={preparation.summary}
          isPulsing={preparation.tone === 'accent'}
        />
        <Text>{preparation.summary}</Text>
      </Stack>
      <MetadataList label={{position: 'start'}}>
        <MetadataListItem label="当前店铺">{preparation.store}</MetadataListItem>
        <MetadataListItem label="调试口">{preparation.debug}</MetadataListItem>
      </MetadataList>
      <Text type="supporting">{preparation.hint}</Text>
    </Stack>
  );
}
