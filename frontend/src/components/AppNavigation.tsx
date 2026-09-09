import {SideNav, SideNavHeading, SideNavItem} from '@astryxdesign/core/SideNav';

/** 操作台各 tab 的导航目的地；自动批准页与操作台页共用。 */
const NAV_ITEMS = [
  {href: '/', label: '总览'},
  {href: '/prepare', label: '运行准备'},
  {href: '/auto-approval', label: '自动批准'},
  {href: '/followups', label: '达人跟进'},
  {href: '/shipments', label: '物流'},
  {href: '/jobs', label: '任务'},
  {href: '/reports', label: '报表'},
  {href: '/diagnostics', label: '诊断'},
];

interface AppNavigationProps {
  /** 当前高亮的导航路径（详情页传所属列表路径）。 */
  activePath: string;
  subheading: string;
}

export function AppNavigation({activePath, subheading}: AppNavigationProps) {
  return (
    <SideNav
      header={
        <SideNavHeading
          heading="ZnSampleAssistant"
          headingHref="/"
          subheading={subheading}
        />
      }
    >
      {NAV_ITEMS.map((item) => (
        <SideNavItem
          key={item.href}
          label={item.label}
          href={item.href}
          isSelected={item.href === activePath}
        />
      ))}
    </SideNav>
  );
}
