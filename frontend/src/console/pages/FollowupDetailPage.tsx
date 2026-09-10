import {Banner} from '@astryxdesign/core/Banner';
import {Button} from '@astryxdesign/core/Button';
import {CheckboxInput} from '@astryxdesign/core/CheckboxInput';
import {Heading} from '@astryxdesign/core/Heading';
import {MetadataList, MetadataListItem} from '@astryxdesign/core/MetadataList';
import {Section} from '@astryxdesign/core/Section';
import {Stack} from '@astryxdesign/core/Stack';
import {Text} from '@astryxdesign/core/Text';
import {TextArea} from '@astryxdesign/core/TextArea';
import {TextInput} from '@astryxdesign/core/TextInput';
import {useState} from 'react';

import {PageHeader} from '../components/PageHeader';
import {StatusToken} from '../components/StatusToken';
import type {FollowupDetailData} from '../types';

export function FollowupDetailPage({data}: {data: FollowupDetailData}) {
  const [contentUrl, setContentUrl] = useState('');
  const [writeFeishuOnContent, setWriteFeishuOnContent] = useState(false);
  const [writeFeishuOnUnfulfilled, setWriteFeishuOnUnfulfilled] = useState(false);

  return (
    <Stack gap={5}>
      <PageHeader eyebrow="跟进预览" title="跟进预览" description={data.creator_name} />

      <MetadataList label={{position: 'start'}}>
        <MetadataListItem label="达人">{data.creator_name}</MetadataListItem>
        <MetadataListItem label="商品">{data.product_id}</MetadataListItem>
        <MetadataListItem label="订单号">{data.main_order_id}</MetadataListItem>
        <MetadataListItem label="物流单号">{data.tracking_display}</MetadataListItem>
        <MetadataListItem label="送达">
          {data.delivered_at !== '' ? data.delivered_at : data.scheduled_label}
        </MetadataListItem>
        <MetadataListItem label="样品状态">{data.platform_status_text}</MetadataListItem>
        <MetadataListItem label="达人类型">{data.creator_type_label}</MetadataListItem>
        <MetadataListItem label="阶段">
          <StatusToken label={data.stage_label} tone={data.stage_tone} />
        </MetadataListItem>
        <MetadataListItem label="动作">
          <StatusToken label={data.action_label} tone={data.action_tone} />
        </MetadataListItem>
        <MetadataListItem label="状态">
          <StatusToken
            label={data.status_label}
            tone={data.status_tone}
            tooltip={data.status_tooltip}
          />
        </MetadataListItem>
        {data.send_result_label !== '' && (
          <MetadataListItem label="发送">{data.send_result_label}</MetadataListItem>
        )}
        {data.note !== '' && (
          <MetadataListItem label={data.status === 'needs_review' ? '原因' : '说明'}>
            {data.status === 'needs_review' ? data.review_label : data.note}
          </MetadataListItem>
        )}
      </MetadataList>

      <Stack gap={2}>
        <Heading level={2}>消息预览</Heading>
        <TextArea label="消息预览" isLabelHidden value={data.message_preview} isReadOnly rows={16} />
        {data.attachment_url !== '' && (
          <img src={data.attachment_url} alt="B005 商品说明" width="100%" />
        )}
      </Stack>

      <Section>
        <Stack gap={2}>
          <Heading level={2}>本地人工确认</Heading>
          <Stack direction="horizontal" gap={2} wrap="wrap">
            <form method="post" action={`/api/followups/${data.id}/set-type`}>
              <Button type="submit" name="creator_type" value="video" label="按视频达人跟进" />
            </form>
            <form method="post" action={`/api/followups/${data.id}/set-type`}>
              <Button type="submit" name="creator_type" value="live" label="按直播达人跟进" />
            </form>
            <form method="post" action={`/api/followups/${data.id}/set-lang`}>
              <Button type="submit" name="lang" value="en" label="英语" />
            </form>
            <form method="post" action={`/api/followups/${data.id}/set-lang`}>
              <Button type="submit" name="lang" value="es" label="西班牙语" />
            </form>
            <form method="post" action={`/api/followups/${data.id}/skip`}>
              <Button type="submit" label="跳过" variant="destructive" />
            </form>
          </Stack>
          <Banner
            status="info"
            title="只改本地预览"
            description="以上操作只更新本地预览，本页不会发送消息。视频+直播达人默认使用视频达人话术；如需改成直播话术，再点「按直播达人跟进」。"
          />
        </Stack>
      </Section>

      {data.can_send && (
        <Section>
          <Stack gap={2}>
            <Heading level={2}>跟进私信进度</Heading>
            {data.action_completed ? (
              <Text>已在本地标记为「已发跟进私信」。这不是平台发送回执，也不会撤回。</Text>
            ) : data.status === 'needs_review' ? (
              <Text>先完成上方的人工确认，再预演或标记已发跟进私信。预演只会打开会话，不会发送。</Text>
            ) : (
              <>
                <Text>可先预演打开会话并核对话术（不会发送）。人工发出后，再点这里记成本地完成。</Text>
                <Stack direction="horizontal" gap={2}>
                  <form method="post" action={`/api/followups/${data.id}/preview-send`}>
                    <Button type="submit" label="预演跟进私信" variant="secondary" />
                  </form>
                  <form method="post" action={`/api/followups/${data.id}/mark-sent`}>
                    <Button type="submit" label="标记已发跟进私信" variant="primary" />
                  </form>
                </Stack>
              </>
            )}
          </Stack>
        </Section>
      )}

      {data.can_list && (
        <Section>
          <Stack gap={2}>
            <Heading level={2}>D+10 名单进度</Heading>
            {data.action_completed ? (
              <Text>已在本地标记为「已出名单给业务」。</Text>
            ) : data.status === 'needs_review' ? (
              <Text>先完成上方的人工确认，再标记已出名单给业务。不会自动发私信。</Text>
            ) : (
              <>
                <Text>把这份名单交给业务后，再点这里记成本地完成。不会自动发私信。</Text>
                <form method="post" action={`/api/followups/${data.id}/mark-listed`}>
                  <Button type="submit" label="标记已出名单给业务" variant="primary" />
                </form>
              </>
            )}
          </Stack>
        </Section>
      )}

      <Section>
        <Stack gap={2}>
          <Heading level={2}>确认达人已出内容</Heading>
          <Text>
            确认后会生成本地「内容感谢」预览，并把该样品标为已完成。只有勾选写飞书开关时，才会把合作状态改为「已完成」。
          </Text>
          <form method="post" action={`/api/followups/${data.id}/confirm-content`}>
            <Stack gap={2}>
              <TextInput
                label="内容链接（可选）"
                htmlName="content_url"
                value={contentUrl}
                onChange={setContentUrl}
                placeholder="https://..."
              />
              <CheckboxInput
                label="同时写飞书「已完成」"
                htmlName="write_feishu"
                value={writeFeishuOnContent}
                onChange={setWriteFeishuOnContent}
              />
              <Stack direction="horizontal" gap={2}>
                <Button type="submit" name="content_type" value="video" label="确认已出视频" />
                <Button type="submit" name="content_type" value="live" label="确认已开直播" />
              </Stack>
            </Stack>
          </form>
        </Stack>
      </Section>

      {data.is_unfulfilled_stage && (
        <Section>
          <Stack gap={2}>
            <Heading level={2}>标记未履约</Heading>
            <Text>
              默认只记本地结果。勾选开关后才会把飞书合作状态从「待发布」改为「未发布」。
            </Text>
            <form method="post" action={`/api/followups/${data.id}/mark-unfulfilled`}>
              <Stack gap={2}>
                <CheckboxInput
                  label="同时写飞书「未发布」"
                  htmlName="write_feishu"
                  value={writeFeishuOnUnfulfilled}
                  onChange={setWriteFeishuOnUnfulfilled}
                />
                <Stack direction="horizontal">
                  <Button type="submit" label="标记未履约" variant="destructive" />
                </Stack>
              </Stack>
            </form>
          </Stack>
        </Section>
      )}
    </Stack>
  );
}
