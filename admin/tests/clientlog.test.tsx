import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';

import { saveToken } from '@/api/client';
import { ClientLog } from '@/screens/ClientLog';

import { mockApi, renderPanel } from './helpers';

const gtk = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15';
const crash = {
  kind: 'crash',
  session: 'abc123',
  platform: 'tdesktop',
  tg_version: '8.0',
  ua: gtk,
  app_version: '',
  stage: 'render',
  message: 'previous start (old1) stopped at "render" on #/',
  stack: '',
  path: '#/',
  user_id: 42,
  at: 1790000000,
};

beforeEach(() => {
  saveToken('admin');
});

describe('device log', () => {
  it('shows crashes with the engine and how far the app got', async () => {
    const api = mockApi({
      'GET /admin/client-log': {
        summary: { tdesktop: { boot: 12, crash: 3, error: 1 } },
        items: [crash],
      },
    });
    renderPanel(<ClientLog />);
    expect(await screen.findByText('3 کرش از 12 شروع')).toBeInTheDocument();
    expect(screen.getByText('WebKitGTK')).toBeInTheDocument();
    expect(screen.getByText('حین رندر اول')).toBeInTheDocument();
    await userEvent.click(screen.getByText(crash.message));
    expect(screen.getByText(/session abc123/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'کرش' }));
    expect(api.calls.some((call) => call.url.includes('kind=crash'))).toBe(true);
  });
});
