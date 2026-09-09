import {Stack} from '@astryxdesign/core/Stack';
import {StatusDot} from '@astryxdesign/core/StatusDot';
import {Text} from '@astryxdesign/core/Text';

import type {StatusTone} from '../types';

interface StatusTokenProps {
  label: string;
  tone: StatusTone;
  tooltip?: string;
}

/** Astryx 的状态呈现：StatusDot（语义色）+ 可见文字。 */
export function StatusToken({label, tone, tooltip}: StatusTokenProps) {
  if (label === '' || label === '-') {
    return <Text type="supporting">-</Text>;
  }
  return (
    <Stack direction="horizontal" gap={1} vAlign="center">
      <StatusDot variant={tone} label={label} tooltip={tooltip || undefined} />
      <Text>{label}</Text>
    </Stack>
  );
}
