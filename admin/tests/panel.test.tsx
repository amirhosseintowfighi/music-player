import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { App } from '@/App';
import { ApiError, readToken, saveToken, setUnauthorizedHandler } from '@/api/client';
import { Broadcasts } from '@/screens/Broadcasts';
import { Dashboard } from '@/screens/Dashboard';
import { Payments } from '@/screens/Payments';
import { Users } from '@/screens/Users';

import { makeMe, mockApi, renderPanel } from './helpers';

const overview = {
  dau: 120,
  wau: 400,
  mau: 900,
  new_users_today: 12,
  paying_users: 45,
  trials: 5,
  tracks: 5400,
  channels: 30,
  plays_today: 800,
  revenue_30d: [{ provider: 'zarinpal', currency: 'IRR', amount: 67050000 }],
  conversion_pct: 5.0,
  churn_30d_pct: 1.2,
};

const userRow = {
  id: 7,
  tg_id: 9001,
  username: 'sara',
  first_name: 'سارا',
  lang: 'fa',
  plan_code: 'free',
  premium_until: null,
  is_banned: false,
  ban_reason: null,
  bot_blocked: false,
  created_at: '2026-09-01T10:00:00Z',
  last_seen_at: '2026-09-18T08:00:00Z',
};

const userDetail = {
  user: userRow,
  stats: { channels: 3, playlists: 2, likes: 40, plays: 300, spent: 0 },
  subscription_status: 'free',
  subscription_expires_at: null,
};

beforeEach(() => {
  saveToken(null);
  setUnauthorizedHandler(null);
  vi.unstubAllGlobals();
});

describe('dashboard', () => {
  it('shows the headline numbers from the API', async () => {
    mockApi({
      'GET /admin/overview': overview,
      'GET /admin/metrics/users': [{ day: '2026-09-17', value: 5 }],
      'GET /admin/retention': [{ cohort: '2026-09-14', size: 10, weeks: { '0': 10, '1': 4 } }],
    });
    renderPanel(<Dashboard />);

    expect(await screen.findByText('۱۲۰')).toBeInTheDocument();
    expect(screen.getByText('۹۰۰')).toBeInTheDocument();
    expect(screen.getByText('5%')).toBeInTheDocument();
    // Rial is shown as toman.
    expect(screen.getByText(/۶٬۷۰۵٬۰۰۰ تومان/)).toBeInTheDocument();
    expect(await screen.findByText('40%')).toBeInTheDocument(); // W1 retention
  });

  it('switches the chart metric', async () => {
    const api = mockApi({
      'GET /admin/overview': overview,
      'GET /admin/metrics/users': [{ day: '2026-09-17', value: 5 }],
      'GET /admin/metrics/revenue': [{ day: '2026-09-17', value: 100 }],
      'GET /admin/retention': [],
    });
    renderPanel(<Dashboard />);

    await userEvent.click(await screen.findByText('درآمد'));
    await waitFor(() =>
      expect(api.calls.some((call) => call.url.includes('/metrics/revenue'))).toBe(true),
    );
  });
});

describe('users', () => {
  it('searches and opens a user', async () => {
    const api = mockApi({
      'GET /admin/users': { items: [userRow], total: 1 },
      'GET /admin/users/7': userDetail,
    });
    renderPanel(<Users me={makeMe()} />);

    await userEvent.type(screen.getByPlaceholderText(/جستجو/), 'sara');
    await waitFor(() =>
      expect(api.calls.some((call) => call.url.includes('q=sara'))).toBe(true),
    );

    await userEvent.click((await screen.findAllByText('جزئیات'))[0]!);
    expect(await screen.findByText(/tg 9001/)).toBeInTheDocument();
  });

  it('bans a user with a reason', async () => {
    const api = mockApi({
      'GET /admin/users': { items: [userRow], total: 1 },
      'GET /admin/users/7': userDetail,
      'POST /admin/users/7/ban': { ...userRow, is_banned: true },
    });
    renderPanel(<Users me={makeMe()} />);

    await userEvent.click((await screen.findAllByText('جزئیات'))[0]!);
    await userEvent.type(await screen.findByPlaceholderText('دلیل'), 'spam');
    await userEvent.click(screen.getByText('مسدود کردن'));

    await waitFor(() => {
      const call = api.calls.find((entry) => entry.url.includes('/ban'));
      expect(call?.body).toEqual({ banned: true, reason: 'spam' });
    });
  });

  it('hides the edit controls from an admin without the permission', async () => {
    mockApi({
      'GET /admin/users': { items: [userRow], total: 1 },
      'GET /admin/users/7': userDetail,
    });
    renderPanel(<Users me={makeMe({ role: 'support', permissions: ['users.view'] })} />);

    await userEvent.click((await screen.findAllByText('جزئیات'))[0]!);
    await screen.findByText(/tg 9001/);
    expect(screen.queryByText('مسدود کردن')).not.toBeInTheDocument();
    expect(screen.queryByText(/ورود به‌جای کاربر/)).not.toBeInTheDocument();
  });
});

describe('payments review', () => {
  const payment = {
    id: 3,
    public_id: 'abcd',
    amount: 1490000,
    currency: 'IRR',
    plan_code: 'pro_monthly',
    receipt_file_id: 'AgACPhoto',
    review_due_at: '2026-09-19T08:00:00Z',
    created_at: '2026-09-18T08:00:00Z',
    user_id: 7,
    tg_id: 9001,
    first_name: 'سارا',
    username: 'sara',
  };

  it('approves a card transfer', async () => {
    const api = mockApi({
      'GET /admin/payments/pending': [payment],
      'POST /admin/payments/3/approve': { status: 'paid' },
    });
    renderPanel(<Payments />);

    expect(await screen.findByText(/۱۴۹٬۰۰۰ تومان/)).toBeInTheDocument();
    await userEvent.click(screen.getByText('تأیید'));
    await waitFor(() =>
      expect(api.calls.some((call) => call.url.endsWith('/approve'))).toBe(true),
    );
  });

  it('rejects with a reason', async () => {
    const api = mockApi({
      'GET /admin/payments/pending': [payment],
      'POST /admin/payments/3/reject': { status: 'rejected' },
    });
    renderPanel(<Payments />);

    await userEvent.type(await screen.findByPlaceholderText('دلیل رد'), 'blurry');
    await userEvent.click(screen.getByText('رد'));
    await waitFor(() => {
      const call = api.calls.find((entry) => entry.url.includes('/reject'));
      expect(call?.url).toContain('reason=blurry');
    });
  });

  it('says so when the queue is empty', async () => {
    mockApi({ 'GET /admin/payments/pending': [] });
    renderPanel(<Payments />);
    expect(await screen.findByText('پرداختی در انتظار بررسی نیست')).toBeInTheDocument();
  });
});

describe('broadcasts', () => {
  it('estimates the audience before sending', async () => {
    const api = mockApi({
      'GET /admin/broadcasts': [],
      'POST /admin/broadcasts/estimate': { total: 1234 },
    });
    renderPanel(<Broadcasts />);

    await userEvent.selectOptions(await screen.findByLabelText('plan'), 'free');
    await userEvent.click(screen.getByText('تخمین مخاطب'));

    expect(await screen.findByText(/۱٬۲۳۴ گیرنده/)).toBeInTheDocument();
    const call = api.calls.find((entry) => entry.url.includes('/estimate'));
    expect(call?.body).toEqual({ plan: 'free' });
  });

  it('refuses to create a broadcast with no text', async () => {
    const api = mockApi({ 'GET /admin/broadcasts': [] });
    renderPanel(<Broadcasts />);

    await userEvent.click(await screen.findByText('ساختن پیش‌نویس'));
    expect(await screen.findByText('حداقل یک متن لازم است')).toBeInTheDocument();
    expect(api.calls.filter((call) => call.method === 'POST')).toHaveLength(0);
  });

  it('creates an A/B broadcast', async () => {
    const api = mockApi({
      'GET /admin/broadcasts': [],
      'POST /admin/broadcasts': {
        id: 1,
        status: 'draft',
        total: 10,
        sent: 0,
        failed: 0,
        blocked: 0,
        created_at: '2026-09-18T08:00:00Z',
      },
    });
    renderPanel(<Broadcasts />);

    await userEvent.type(await screen.findByLabelText('variant-0'), 'سلام');
    await userEvent.click(screen.getByText('افزودن نسخهٔ A/B'));
    await userEvent.type(await screen.findByLabelText('variant-1'), 'درود');
    await userEvent.click(screen.getByText('ساختن پیش‌نویس'));

    await waitFor(() => {
      const call = api.calls.find(
        (entry) => entry.method === 'POST' && entry.url.endsWith('/admin/broadcasts'),
      );
      expect((call?.body as { variants: unknown[] }).variants).toHaveLength(2);
    });
  });

  it('shows live progress for a running broadcast', async () => {
    mockApi({
      'GET /admin/broadcasts': [
        {
          id: 4,
          status: 'running',
          total: 200,
          sent: 50,
          failed: 0,
          blocked: 10,
          created_at: '2026-09-18T08:00:00Z',
        },
      ],
    });
    renderPanel(<Broadcasts />);
    expect(await screen.findByText(/30%/)).toBeInTheDocument();
    expect(screen.getByText('running')).toBeInTheDocument();
  });
});

describe('session', () => {
  it('shows the login screen when there is no token', async () => {
    mockApi({});
    render(<App />);
    expect(await screen.findByText('پنل مدیریت TMusic')).toBeInTheDocument();
  });

  it('drops the token and returns to login on a 401', async () => {
    saveToken('stale-token');
    mockApi({
      'GET /admin/me': new Response(
        JSON.stringify({ error: { code: 'unauthorized', message: 'no' } }),
        { status: 401 },
      ),
    });
    render(<App />);
    expect(await screen.findByText('پنل مدیریت TMusic')).toBeInTheDocument();
    expect(readToken()).toBeNull();
  });

  it('only shows the tabs the role is allowed to see', async () => {
    saveToken('support-token');
    mockApi({
      'GET /admin/me': makeMe({
        role: 'support',
        permissions: ['dashboard.view', 'users.view', 'payments.review'],
      }),
      'GET /admin/overview': overview,
      'GET /admin/metrics/users': [],
      'GET /admin/retention': [],
    });
    render(<App />);

    expect(await screen.findByText('کاربران')).toBeInTheDocument();
    expect(screen.getByText('پرداخت‌ها')).toBeInTheDocument();
    expect(screen.queryByText('تنظیمات')).not.toBeInTheDocument();
    expect(screen.queryByText('پیام همگانی')).not.toBeInTheDocument();
  });
});

describe('api client', () => {
  it('turns an error body into an ApiError', async () => {
    mockApi({
      'GET /admin/overview': new Response(
        JSON.stringify({ error: { code: 'forbidden', message: 'missing permission' } }),
        { status: 403 },
      ),
    });
    const { get } = await import('@/api/client');
    await expect(get('/admin/overview')).rejects.toBeInstanceOf(ApiError);
  });
});
