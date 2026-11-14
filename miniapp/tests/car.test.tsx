/** Car mode: three huge controls, the like, and the screen kept on. */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { __setAuthForTests } from '@/api/client';
import { CarMode } from '@/components/CarMode';
import { usePlayer } from '@/store/player';
import { useUi } from '@/store/ui';

import { authRoutes, makeTrack, mockApi, renderApp } from './helpers';

afterEach(() => vi.restoreAllMocks());

describe('car mode', () => {
  it('drives the player with big buttons and keeps the screen on', async () => {
    __setAuthForTests('token');
    const request = vi.fn(async () => ({ release: async () => undefined }));
    Object.defineProperty(navigator, 'wakeLock', { configurable: true, value: { request } });
    const api = mockApi({ ...authRoutes(), 'PUT /v1/tracks/7/like': { liked: true, likes_count: 1 } });
    const next = vi.fn(async () => undefined);
    const toggle = vi.fn(async () => undefined);
    usePlayer.setState({ current: makeTrack({ id: 7, title: 'Jaddeh' }), next, toggle, position: 50, duration: 100 });
    useUi.setState({ carMode: true });

    renderApp(<CarMode />);
    expect(screen.getByText('Jaddeh')).toBeTruthy();
    expect(request).toHaveBeenCalledWith('screen');

    await userEvent.click(screen.getByRole('button', { name: 'next' }));
    expect(next).toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: 'پخش' }));
    expect(toggle).toHaveBeenCalled();

    const like = screen.getByRole('button', { name: 'لایک' });
    await userEvent.click(like);
    expect(like.getAttribute('aria-pressed')).toBe('true');
    await waitFor(() =>
      expect(api.calls.some((c) => c.method === 'PUT' && c.url.endsWith('/v1/tracks/7/like'))).toBe(true),
    );

    await userEvent.click(screen.getByRole('button', { name: 'بستن' }));
    expect(useUi.getState().carMode).toBe(false);
  });
});
