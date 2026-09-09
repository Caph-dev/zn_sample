import {Badge} from '@astryxdesign/core/Badge';
import {Item} from '@astryxdesign/core/Item';
import {List} from '@astryxdesign/core/List';
import {MetadataList, MetadataListItem} from '@astryxdesign/core/MetadataList';
import {Section} from '@astryxdesign/core/Section';
import {Stack} from '@astryxdesign/core/Stack';
import {Text} from '@astryxdesign/core/Text';

import {PageHeader} from '../components/PageHeader';
import {StatusToken} from '../components/StatusToken';
import type {DiagnosticsData} from '../types';

function stateTone(state: string): 'success' | 'warning' | 'error' {
  if (state === 'READY') {
    return 'success';
  }
  return state === 'ZINIAO_BUSY' ? 'warning' : 'error';
}

export function DiagnosticsPage({data}: {data: DiagnosticsData}) {
  return (
    <Stack gap={5}>
      <PageHeader
        eyebrow="本机"
        title="本机诊断"
        description="只读检查运行环境；不会打开店铺或触发任何写操作。"
      />

      <MetadataList label={{position: 'start'}}>
        <MetadataListItem label="Python">{data.python_version}</MetadataListItem>
        <MetadataListItem label="紫鸟 CLI">
          <StatusToken label={data.cli_state} tone={stateTone(data.cli_state)} />
        </MetadataListItem>
        <MetadataListItem label="Bridge">
          <StatusToken label={data.bridge_state} tone={stateTone(data.bridge_state)} />
        </MetadataListItem>
        <MetadataListItem label="配置文件">
          {data.config_exists ? '已配置' : '未配置'}
        </MetadataListItem>
        <MetadataListItem label="飞书">
          {data.feishu_configured ? '已配置' : '未配置'}
        </MetadataListItem>
      </MetadataList>

      <Section>
        <Stack gap={2}>
          <Text type="supporting">运行店铺</Text>
          {data.stores.length === 0 ? (
            <Text type="supporting">
              {data.store_error === 'running-query-failed'
                ? '暂时无法读取紫鸟状态'
                : '未检测到运行店铺'}
            </Text>
          ) : (
            <List hasDividers>
              {data.stores.map((store) => (
                <Item
                  key={store.storeId}
                  as="li"
                  label={store.storeName}
                  endContent={<Badge label={store.storeId} />}
                />
              ))}
            </List>
          )}
        </Stack>
      </Section>
    </Stack>
  );
}
