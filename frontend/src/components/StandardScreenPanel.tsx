import {Badge} from '@astryxdesign/core/Badge';
import {Banner} from '@astryxdesign/core/Banner';
import {Button} from '@astryxdesign/core/Button';
import {Heading} from '@astryxdesign/core/Heading';
import {Item} from '@astryxdesign/core/Item';
import {List} from '@astryxdesign/core/List';
import {Section} from '@astryxdesign/core/Section';
import {Stack} from '@astryxdesign/core/Stack';

import type {OperatorGroupPayload, OperatorItemPayload} from '../types';

/**
 * 标准 SOP 只读名单入口：正式筛查/批准仍只走脚本，
 * 这里只导出待批准名单，不会批准、写飞书或发私信。
 */
function ScreenAction({item}: {item: OperatorItemPayload}) {
  if (item.href !== '') {
    return <Button label={item.button_label} href={item.href} variant={item.variant} />;
  }
  return (
    <form method="post" action={item.action} data-job-form data-job-label={item.job_label}>
      <Button type="submit" label={item.button_label} variant={item.variant} />
    </form>
  );
}

export function StandardScreenPanel({group}: {group: OperatorGroupPayload}) {
  return (
    <Section>
      <Stack gap={2}>
        <Heading level={2}>{group.heading}</Heading>
        {group.note !== '' && <Banner status="info" title="说明" description={group.note} />}
        <List hasDividers>
          {group.items.map((item) => (
            <Item
              key={item.title}
              as="li"
              label={item.title}
              description={item.description}
              startContent={item.index !== '' ? <Badge label={item.index} /> : undefined}
              endContent={<ScreenAction item={item} />}
            />
          ))}
        </List>
      </Stack>
    </Section>
  );
}
