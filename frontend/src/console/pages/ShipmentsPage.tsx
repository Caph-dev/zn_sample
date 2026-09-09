import {EmptyState} from '@astryxdesign/core/EmptyState';
import {Link} from '@astryxdesign/core/Link';
import {Stack} from '@astryxdesign/core/Stack';
import {Tab, TabList} from '@astryxdesign/core/TabList';
import {Table, pixel, proportional} from '@astryxdesign/core/Table';
import {Text} from '@astryxdesign/core/Text';

import {PageHeader} from '../components/PageHeader';
import {StatusToken} from '../components/StatusToken';
import type {ShipmentRow, ShipmentsData} from '../types';

function shipmentHref(value: string): string {
  return value === '' ? '/shipments' : `/shipments?status=${encodeURIComponent(value)}`;
}

export function ShipmentsPage({data}: {data: ShipmentsData}) {
  return (
    <Stack gap={4}>
      <PageHeader
        eyebrow="只读"
        title="物流"
        description="读取免费样品物流与到货状态；本页不写飞书、不发私信。"
      />

      <TabList
        value={data.status_filter}
        onChange={(value) => {
          window.location.assign(shipmentHref(value));
        }}
      >
        {data.quick_filters.map((filter) => (
          <Tab key={filter.value} value={filter.value} label={filter.label} />
        ))}
      </TabList>

      {data.rows.length === 0 ? (
        <EmptyState
          title="还没有物流记录"
          description="先在首页运行一次「同步物流与到货状态」。"
        />
      ) : (
        <Table
          data={data.rows as unknown as Record<string, unknown>[]}
          idKey="id"
          density="compact"
          hasHover
          columns={[
            {
              key: 'creator_name',
              header: '达人',
              width: proportional(2),
              renderCell: (row) => {
                const shipment = row as unknown as ShipmentRow;
                return <Link href={`/shipments/${shipment.id}`}>{shipment.creator_name}</Link>;
              },
            },
            {key: 'main_order_id', header: '订单号', width: proportional(2)},
            {
              key: 'tracking_display',
              header: '物流单号',
              width: proportional(3),
              renderCell: (row) => (
                <Text>{String((row as unknown as ShipmentRow).tracking_display)}</Text>
              ),
            },
            {
              key: 'status',
              header: '状态',
              width: pixel(130),
              renderCell: (row) => {
                const shipment = row as unknown as ShipmentRow;
                return (
                  <StatusToken label={shipment.status_label} tone={shipment.status_tone} />
                );
              },
            },
            {key: 'estimated_delivery_at', header: '预计送达', width: proportional(2)},
            {
              key: 'delivered_at',
              header: '实际送达',
              width: proportional(2),
              renderCell: (row) => {
                const shipment = row as unknown as ShipmentRow;
                if (shipment.delivered_at !== '') {
                  return <Text>{shipment.delivered_at}</Text>;
                }
                if (shipment.needs_delivery_confirmation) {
                  return <Text>待确认送达日</Text>;
                }
                return <Text type="supporting">未送达</Text>;
              },
            },
          ]}
        />
      )}
    </Stack>
  );
}
