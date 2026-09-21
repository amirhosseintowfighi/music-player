import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { saveToken, setUnauthorizedHandler } from '@/api/client';
import { Metadata } from '@/screens/Metadata';

import { mockApi, renderPanel } from './helpers';

const unsure = {
  id: 11,
  title: 'Track 07',
  artists: '',
  metadata_confidence: 20,
  file_name: 'track_07.mp3',
  album: null,
  reports: 0,
  channel_title: 'Persian Hits',
};

const reported = {
  id: 12,
  title: 'Pol',
  artists: 'Moien Official',
  metadata_confidence: 95,
  file_name: null,
  album: null,
  reports: 3,
  channel_title: 'Persian Hits',
};

beforeEach(() => {
  saveToken(null);
  setUnauthorizedHandler(null);
  vi.unstubAllGlobals();
});

describe('metadata review queue', () => {
  it('shows what needs a human, with why', async () => {
    mockApi({ 'GET /admin/metadata/queue': [reported, unsure] });
    renderPanel(<Metadata />);

    expect(await screen.findByDisplayValue('Track 07')).toBeInTheDocument();
    expect(screen.getByDisplayValue('Moien Official')).toBeInTheDocument();
    expect(screen.getByText('۳ گزارش')).toBeInTheDocument();
    expect(screen.getByText(/Persian Hits · track_07.mp3/)).toBeInTheDocument();
  });

  it('will not save until something actually changed', async () => {
    mockApi({ 'GET /admin/metadata/queue': [unsure] });
    renderPanel(<Metadata />);

    const save = await screen.findByRole('button', { name: 'ذخیره' });
    expect(save).toBeDisabled();

    await userEvent.type(screen.getByLabelText('خواننده 11'), 'معین');
    expect(save).toBeEnabled();
  });

  it('fixes one track', async () => {
    const api = mockApi({
      'GET /admin/metadata/queue': [unsure],
      'POST /admin/metadata/11/fix': { tracks: 1 },
    });
    renderPanel(<Metadata />);

    await userEvent.type(await screen.findByLabelText('خواننده 11'), 'معین');
    await userEvent.click(screen.getByRole('button', { name: 'ذخیره' }));

    await waitFor(() => {
      const call = api.calls.find((entry) => entry.url.endsWith('/admin/metadata/11/fix'));
      expect(call?.body).toMatchObject({ artist: 'معین', apply_to_artist: false });
    });
    expect(await screen.findByText('۱ ترک اصلاح شد')).toBeInTheDocument();
  });

  it('applies one correction to every track with the same wrong artist', async () => {
    const api = mockApi({
      'GET /admin/metadata/queue': [reported],
      'POST /admin/metadata/12/fix': { tracks: 42 },
    });
    renderPanel(<Metadata />);

    const artist = await screen.findByLabelText('خواننده 12');
    await userEvent.clear(artist);
    await userEvent.type(artist, 'معین');
    await userEvent.click(screen.getByLabelText(/روی همهٔ ترک‌های این خواننده/));
    await userEvent.click(screen.getByRole('button', { name: 'ذخیره' }));

    await waitFor(() => {
      const call = api.calls.find((entry) => entry.url.endsWith('/admin/metadata/12/fix'));
      expect(call?.body).toMatchObject({ artist: 'معین', apply_to_artist: true });
    });
    expect(await screen.findByText('۴۲ ترک اصلاح شد')).toBeInTheDocument();
  });

  it('filters by where the doubt came from', async () => {
    const api = mockApi({ 'GET /admin/metadata/queue': [reported] });
    renderPanel(<Metadata />);

    await userEvent.click(await screen.findByRole('button', { name: 'گزارش کاربران' }));
    await waitFor(() =>
      expect(api.calls.some((entry) => entry.url.includes('source=reported'))).toBe(true),
    );
  });
});
