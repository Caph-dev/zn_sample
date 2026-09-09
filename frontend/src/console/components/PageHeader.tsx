import {Heading} from '@astryxdesign/core/Heading';
import {Stack} from '@astryxdesign/core/Stack';
import {Text} from '@astryxdesign/core/Text';
import type {ReactNode} from 'react';

interface PageHeaderProps {
  eyebrow: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}

export function PageHeader({eyebrow, title, description, actions}: PageHeaderProps) {
  return (
    <Stack direction="horizontal" gap={3} vAlign="start" justify="between" wrap="wrap">
      <Stack gap={1}>
        <Text type="supporting">{eyebrow}</Text>
        <Heading level={1}>{title}</Heading>
        {description !== undefined && description !== '' && (
          <Text type="supporting">{description}</Text>
        )}
      </Stack>
      {actions !== undefined && <Stack direction="horizontal">{actions}</Stack>}
    </Stack>
  );
}
