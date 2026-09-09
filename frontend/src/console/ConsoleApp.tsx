import {AppShell} from '@astryxdesign/core/AppShell';
import {Stack} from '@astryxdesign/core/Stack';
import {Text} from '@astryxdesign/core/Text';

import {AppNavigation} from '../components/AppNavigation';
import {DiagnosticsPage} from './pages/DiagnosticsPage';
import {FollowupDetailPage} from './pages/FollowupDetailPage';
import {FollowupsPage} from './pages/FollowupsPage';
import {JobDetailPage} from './pages/JobDetailPage';
import {JobsPage} from './pages/JobsPage';
import {OverviewPage} from './pages/OverviewPage';
import {PreparePage} from './pages/PreparePage';
import {ReportsPage} from './pages/ReportsPage';
import {ShipmentDetailPage} from './pages/ShipmentDetailPage';
import {ShipmentsPage} from './pages/ShipmentsPage';
import type {
  ConsoleBootstrap,
  DiagnosticsData,
  FollowupDetailData,
  FollowupsData,
  JobDetailData,
  JobRow,
  OverviewData,
  PageKey,
  PrepareData,
  ReportsData,
  ShipmentDetailData,
  ShipmentsData,
} from './types';

const PAGE_META: Record<PageKey, {activePath: string; title: string}> = {
  overview: {activePath: '/', title: '总览'},
  prepare: {activePath: '/prepare', title: '运行准备'},
  shipments: {activePath: '/shipments', title: '物流'},
  shipment_detail: {activePath: '/shipments', title: '物流详情'},
  followups: {activePath: '/followups', title: '达人跟进'},
  followup_detail: {activePath: '/followups', title: '跟进预览'},
  jobs: {activePath: '/jobs', title: '任务'},
  job_detail: {activePath: '/jobs', title: '任务详情'},
  reports: {activePath: '/reports', title: '报表'},
  diagnostics: {activePath: '/diagnostics', title: '诊断'},
};

function renderPage(bootstrap: ConsoleBootstrap) {
  switch (bootstrap.page) {
    case 'overview':
      return <OverviewPage data={bootstrap.data as OverviewData} />;
    case 'prepare':
      return <PreparePage data={bootstrap.data as PrepareData} />;
    case 'shipments':
      return <ShipmentsPage data={bootstrap.data as ShipmentsData} />;
    case 'shipment_detail':
      return <ShipmentDetailPage data={bootstrap.data as ShipmentDetailData} />;
    case 'followups':
      return <FollowupsPage data={bootstrap.data as FollowupsData} />;
    case 'followup_detail':
      return <FollowupDetailPage data={bootstrap.data as FollowupDetailData} />;
    case 'jobs':
      return <JobsPage data={bootstrap.data as {rows: JobRow[]}} />;
    case 'job_detail':
      return <JobDetailPage data={bootstrap.data as JobDetailData} />;
    case 'reports':
      return <ReportsPage data={bootstrap.data as ReportsData} />;
    case 'diagnostics':
      return <DiagnosticsPage data={bootstrap.data as DiagnosticsData} />;
    default:
      return <Text>未知页面</Text>;
  }
}

export function ConsoleApp({bootstrap}: {bootstrap: ConsoleBootstrap}) {
  const meta = PAGE_META[bootstrap.page];
  return (
    <AppShell
      height="auto"
      variant="section"
      contentPadding={4}
      sideNav={
        <AppNavigation activePath={meta.activePath} subheading="样品业务 · 运营操作台" />
      }
    >
      <Stack gap={4}>{renderPage(bootstrap)}</Stack>
    </AppShell>
  );
}
