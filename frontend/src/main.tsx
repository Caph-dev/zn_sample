import '@astryxdesign/core/reset.css';
import '@astryxdesign/core/astryx.css';
import './app.css';

import {StrictMode} from 'react';
import {createRoot} from 'react-dom/client';
import {App} from './App';

const container = document.getElementById('root');
if (container === null) {
  throw new Error('auto-approval mount point missing');
}
createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
