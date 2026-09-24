import { screen } from '@testing-library/react';
import { beforeEach, expect, it } from 'vitest';

import { __setAuthForTests } from '@/api/client';
import { AlbumScreen } from '@/screens/Detail';

import { authRoutes, mockApi, renderApp } from './helpers';

beforeEach(() => {
  __setAuthForTests('token');
});

const track = {
  id: 5,
  title: 'Kavir',
  artists: [{ id: 2, name: 'Sattar', role: 'primary' }],
  album: 'Gol Vazheh',
  duration: 240,
  language: 'fa',
  year: 1977,
  has_thumb: false,
  channels_count: 1,
  playable: true,
};

it('an album opens on its own page', async () => {
  mockApi({
    ...authRoutes(),
    'GET /v1/albums/2': {
      album: { album: 'Gol Vazheh', artist_id: 2, artist_name: 'Sattar', tracks_count: 1 },
      year: 1977,
      items: [track],
    },
  });

  renderApp(<AlbumScreen />, {
    route: '/album/2/Gol%20Vazheh',
    path: '/album/:artistId/:name',
  });

  expect(await screen.findByText('Gol Vazheh')).toBeTruthy();
  expect(await screen.findByText('Kavir')).toBeTruthy();
  // Artist, year and count all belong in one subtitle line.
  expect(await screen.findByText(/Sattar · 1977/)).toBeTruthy();
});
