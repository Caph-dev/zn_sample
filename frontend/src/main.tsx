import '@astryxdesign/core/reset.css';
import '@astryxdesign/core/astryx.css';
import './app.css';

import {InternationalizationProvider} from '@astryxdesign/core/i18n';
import zhCN from '@astryxdesign/core/locales/zh-CN.json';
import {StrictMode} from 'react';
import {createRoot} from 'react-dom/client';
import {App} from './App';
import type {AutoApprovalBootstrap} from './types';

const FALLBACK_BOOTSTRAP: AutoApprovalBootstrap = {
  screen_group: null,
  prepare_href: '/prepare',
};

function readBootstrap(): AutoApprovalBootstrap {
  const node = document.getElementById('auto-approval-bootstrap');
  if (node === null || node.textContent === null) {
    return FALLBACK_BOOTSTRAP;
  }
  try {
    return {...FALLBACK_BOOTSTRAP, ...(JSON.parse(node.textContent) as AutoApprovalBootstrap)};
  } catch {
    return FALLBACK_BOOTSTRAP;
  }
}

const container = document.getElementById('root');
if (container === null) {
  throw new Error('auto-approval mount point missing');
}
createRoot(container).render(
  <StrictMode>
    <InternationalizationProvider locale="zh-CN" messages={{'zh-CN': zhCN}}>
      <App bootstrap={readBootstrap()} />
    </InternationalizationProvider>
  </StrictMode>,
);
