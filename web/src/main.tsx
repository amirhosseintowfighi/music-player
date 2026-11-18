import { createRoot } from 'react-dom/client';

import { App } from './App';
import { listenForInstall } from './lib/install';
import './design/tokens.css';
import './styles/tv.css';

// Before rendering: the browser offers installation once, early.
listenForInstall();

const el = document.getElementById('root');
if (el) createRoot(el).render(<App />);

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => undefined);
  });
}
