import { createRoot } from 'react-dom/client';

import { App } from './App';
import './design/tokens.css';

const el = document.getElementById('root');
if (el) createRoot(el).render(<App />);

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => undefined);
  });
}
