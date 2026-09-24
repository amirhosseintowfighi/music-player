import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { App } from '@/App';
import '@/design/tokens.css';

/**
 * Telegram opens a Mini App at `…/#tgWebAppData=…&tgWebAppVersion=…`, and this app
 * routes on the hash — so the very first screen every user saw was the "no such
 * page" fallback, until they tapped a tab and the hash became a real route.
 *
 * The SDK in index.html is a blocking script, so by the time this module runs it has
 * already read that fragment and exposed initData: the fragment has done its job and
 * can be replaced with a route. Deep links still work — they arrive inside initData
 * as start_param, and App.tsx turns that into a hash of its own.
 */
const fragment = window.location.hash.slice(1);
if (fragment && !fragment.startsWith('/')) {
  window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}#/`);
}

const root = document.getElementById('root');
if (root) {
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}
