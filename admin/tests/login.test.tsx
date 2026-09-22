import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';

import { readToken, saveToken } from '@/api/client';
import { Login } from '@/screens/Login';

import { makeMe, mockApi } from './helpers';

beforeEach(() => {
  saveToken(null);
  vi.unstubAllGlobals();
});

it('signs in with a username and password, with no Telegram involved', async () => {
  const api = mockApi({
    'POST /admin/login/password': {
      access_token: 'admin-token',
      expires_at: 2000000000,
      me: makeMe(),
    },
  });
  const onLoggedIn = vi.fn();
  render(<Login onLoggedIn={onLoggedIn} />);

  await userEvent.type(screen.getByLabelText('username'), 'operator');
  await userEvent.type(screen.getByLabelText('password'), 'correct-horse-battery');
  await userEvent.click(screen.getAllByRole('button', { name: 'ورود' })[0]!);

  await waitFor(() => expect(onLoggedIn).toHaveBeenCalled());
  expect(readToken()).toBe('admin-token');
  expect(api.calls[0]?.body).toEqual({
    username: 'operator',
    password: 'correct-horse-battery',
  });
});

it('shows one message for a rejected login and keeps no token', async () => {
  mockApi({
    'POST /admin/login/password': new Response(
      JSON.stringify({ error: { code: 'forbidden', message: 'bad credentials' } }),
      { status: 403 },
    ),
  });
  render(<Login onLoggedIn={vi.fn()} />);

  await userEvent.type(screen.getByLabelText('username'), 'operator');
  await userEvent.type(screen.getByLabelText('password'), 'wrong-password-here');
  await userEvent.click(screen.getAllByRole('button', { name: 'ورود' })[0]!);

  expect(await screen.findByText(/نام کاربری یا رمز عبور/)).toBeTruthy();
  expect(readToken()).toBeNull();
});
