import {SideNav, SideNavHeading, SideNavItem} from '@astryxdesign/core/SideNav';

/** 与工作台一致的导航目的地；本页高亮「自动审批」。 */
const NAV_ITEMS = [
  {href: '/', label: '工作台'},
  {href: '/shipments', label: '物流'},
  {href: '/followups', label: '跟进'},
  {href: '/auto-approval', label: '自动审批'},
  {href: '/jobs', label: '任务'},
  {href: '/reports', label: '报表'},
  {href: '/diagnostics', label: '诊断'},
];

export function AppNavigation() {
  return (
    <SideNav
      header={
        <SideNavHeading
          heading="ZnSampleAssistant"
          headingHref="/"
          subheading="自动审批 · 自定义审核方案"
        />
      }
    >
      {NAV_ITEMS.map((item) => (
        <SideNavItem
          key={item.href}
          label={item.label}
          href={item.href}
          isSelected={item.href === '/auto-approval'}
        />
      ))}
    </SideNav>
  );
}
