import {MetadataList, MetadataListItem} from '@astryxdesign/core/MetadataList';
import {Stack} from '@astryxdesign/core/Stack';
import {Text} from '@astryxdesign/core/Text';

import {PageHeader} from '../components/PageHeader';
import {StatusToken} from '../components/StatusToken';
import type {ShipmentDetailData} from '../types';

export function ShipmentDetailPage({data}: {data: ShipmentDetailData}) {
  return (
    <Stack gap={4}>
      <PageHeader eyebrow="只读" title="物流详情" description={data.creator_name} />
      <MetadataList label={{position: 'start'}}>
        <MetadataListItem label="达人">{data.creator_name}</MetadataListItem>
        <MetadataListItem label="红人 ID">{data.creator_id}</MetadataListItem>
        <MetadataListItem label="订单号">{data.main_order_id}</MetadataListItem>
        <MetadataListItem label="物流单号">{data.tracking_display}</MetadataListItem>
        <MetadataListItem label="状态">
          <StatusToken label={data.status_label} tone={data.status_tone} />
        </MetadataListItem>
        <MetadataListItem label="预计送达">{data.estimated_delivery_at}</MetadataListItem>
        <MetadataListItem label="实际送达">
          {data.delivered_at !== '' ? (
            data.delivered_at
          ) : data.needs_delivery_confirmation ? (
            '待确认送达日'
          ) : (
            <Text type="supporting">未送达</Text>
          )}
        </MetadataListItem>
      </MetadataList>
    </Stack>
  );
}
