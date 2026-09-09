import '@astryxdesign/core/reset.css';
import '@astryxdesign/core/astryx.css';
import './console.css';

import {StrictMode} from 'react';
import {createRoot} from 'react-dom/client';

import {ConsoleApp} from './ConsoleApp';
import type {ConsoleBootstrap} from './types';

const bootstrapElement = document.getElementById('console-bootstrap');
if (bootstrapElement === null) {
  throw new Error('console bootstrap missing');
}
const bootstrap = JSON.parse(bootstrapElement.textContent ?? '{}') as ConsoleBootstrap;

const container = document.getElementById('console-root');
if (container === null) {
  throw new Error('console mount point missing');
}
createRoot(container).render(
  <StrictMode>
    <ConsoleApp bootstrap={bootstrap} />
  </StrictMode>,
);
