import {Badge} from '@astryxdesign/core/Badge';
import {Banner} from '@astryxdesign/core/Banner';
import {Button} from '@astryxdesign/core/Button';
import {Heading} from '@astryxdesign/core/Heading';
import {Item} from '@astryxdesign/core/Item';
import {List} from '@astryxdesign/core/List';
import {Stack} from '@astryxdesign/core/Stack';

import type {OperatorGroup, OperatorItem} from '../types';

/** 操作入口统一走原生表单 + app.js 委托，确认口令与 force gate 由服务端下发。 */
function OperatorAction({item, prepareReady}: {item: OperatorItem; prepareReady: boolean}) {
  if (item.href !== '') {
    return <Button label={item.button_label} href={item.href} variant={item.variant} />;
  }
  const variant = item.is_prepare && prepareReady ? 'secondary' : item.variant;
  return (
    <form
      method="post"
      action={item.action}
      data-job-form
      data-job-label={item.job_label}
      data-confirm-token={item.confirm?.token}
      data-confirm-title={item.confirm?.title}
      data-confirm-description={item.confirm?.description}
      data-confirm-action={item.confirm?.action}
      data-force-gate={item.force_gate}
    >
      <Button
        type="submit"
        label={item.button_label}
        variant={variant}
        tooltip={
          item.is_prepare && prepareReady
            ? '调试口已就绪；如需重新打开店铺，仍可点击'
            : undefined
        }
      />
    </form>
  );
}

export function OperatorGroupBlock({
  group,
  prepareReady = false,
}: {
  group: OperatorGroup;
  prepareReady?: boolean;
}) {
  return (
    <Stack gap={2}>
      <Heading level={3}>{group.heading}</Heading>
      {group.note !== '' && <Banner status="warning" title="注意" description={group.note} />}
      <List hasDividers>
        {group.items.map((item) => (
          <Item
            key={item.title}
            as="li"
            label={item.title}
            description={item.description}
            startContent={
              item.index !== '' ? (
                <Badge label={item.index} variant={item.kind === 'danger' ? 'error' : 'neutral'} />
              ) : item.kind !== '' ? (
                <Badge label={item.kind} />
              ) : undefined
            }
            endContent={<OperatorAction item={item} prepareReady={prepareReady} />}
          />
        ))}
      </List>
    </Stack>
  );
}

export function OperatorGroups({
  groups,
  prepareReady = false,
}: {
  groups: OperatorGroup[];
  prepareReady?: boolean;
}) {
  return (
    <Stack gap={4}>
      {groups.map((group) => (
        <OperatorGroupBlock key={group.key} group={group} prepareReady={prepareReady} />
      ))}
    </Stack>
  );
}
