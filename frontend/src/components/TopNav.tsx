import {Stack} from '@astryxdesign/core/Stack';
import {Link} from '@astryxdesign/core/Link';

const NAV_ITEMS = [
  {href: '/', label: '工作台'},
  {href: '/shipments', label: '物流'},
  {href: '/followups', label: '跟进'},
  {href: '/auto-approval', label: '自动审批'},
  {href: '/jobs', label: '任务'},
  {href: '/reports', label: '报表'},
  {href: '/diagnostics', label: '诊断'},
];

export function TopNav() {
  return (
    <Stack
      direction="horizontal"
      gap={2}
      padding={3}
      hAlign="between"
      vAlign="center"
      width="100%"
    >
      <Stack direction="horizontal" gap={2} vAlign="center">
        <Link href="/">ZnSampleAssistant</Link>
        <span className="nav-current">自动审批 · 自定义审核方案</span>
      </Stack>
      <Stack direction="horizontal" gap={2} vAlign="center">
        {NAV_ITEMS.map((item) => (
          <Link key={item.href} href={item.href}>
            {item.label}
          </Link>
        ))}
      </Stack>
    </Stack>
  );
}
