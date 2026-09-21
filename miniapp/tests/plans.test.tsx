import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { __setAuthForTests } from '@/api/client';
import { Plans } from '@/screens/Plans';
import { useUi } from '@/store/ui';

import { authRoutes, mockApi, renderApp } from './helpers';

const plans = {
  plans: [
    {
      code: 'free',
      name: 'رایگان',
      period_days: null,
      prices: {},
      limits: { channels: 3 },
      features: ['discover_weekly'],
      is_current: true,
    },
    {
      code: 'pro_monthly',
      name: 'پرو ماهانه',
      period_days: 30,
      prices: { IRR: 1490000, XTR: 150 },
      limits: { channels: -1 },
      features: ['offline', 'share_playlist'],
      is_current: false,
    },
    {
      code: 'pro_yearly',
      name: 'پرو سالانه',
      period_days: 365,
      prices: { IRR: 14900000, XTR: 1500 },
      limits: { channels: -1 },
      features: ['offline', 'share_playlist'],
      is_current: false,
    },
  ],
  providers: [
    { code: 'stars', currency: 'XTR', kind: 'telegram_invoice' as const },
    { code: 'zarinpal', currency: 'IRR', kind: 'redirect' as const },
    { code: 'card2card', currency: 'IRR', kind: 'instructions' as const },
  ],
  trial_available: true,
  trial_days: 7,
};

const freeSubscription = { plan_code: 'free', status: 'free' as const, days_left: 0, auto_renew: false };

function routes(extra: Record<string, unknown> = {}) {
  return {
    ...authRoutes(),
    'GET /v1/plans': plans,
    'GET /v1/me/subscription': freeSubscription,
    ...extra,
  };
}

beforeEach(() => {
  __setAuthForTests('token');
  useUi.setState({ toasts: [], upsell: null });
  vi.unstubAllGlobals();
});

describe('Plans screen', () => {
  it('shows the plans and the enabled gateways from the server', async () => {
    mockApi(routes());
    renderApp(<Plans />);

    expect(await screen.findByText('پرو ماهانه')).toBeInTheDocument();
    expect(screen.getByText('پرو سالانه')).toBeInTheDocument();
    // The free plan is not a purchasable option.
    expect(screen.queryByText('رایگان')).not.toBeInTheDocument();
    // Rial prices are shown in toman.
    expect(screen.getByText(/۱۴۹٬۰۰۰|149,000/)).toBeInTheDocument();
    expect(screen.getByText('استارز تلگرام')).toBeInTheDocument();
    expect(screen.getByText('زرین‌پال')).toBeInTheDocument();
    expect(screen.getByText('کارت به کارت')).toBeInTheDocument();
  });

  it('opens a Stars invoice and refreshes after a paid result', async () => {
    const openInvoice = vi.fn((_url: string, callback: (status: string) => void) => callback('paid'));
    vi.stubGlobal('Telegram', { WebApp: { initData: 'x', openInvoice } });
    const api = mockApi(
      routes({
        'POST /v1/payments/checkout': {
          payment_id: 'p-1',
          provider: 'stars',
          amount: 150,
          currency: 'XTR',
          kind: 'telegram_invoice',
          url: 'https://t.me/$invoice',
          payload: null,
        },
      }),
    );
    renderApp(<Plans />);

    await userEvent.click(await screen.findByText('استارز تلگرام'));
    await waitFor(() => expect(openInvoice).toHaveBeenCalledWith('https://t.me/$invoice', expect.any(Function)));

    const call = api.calls.find((c) => c.url.includes('/payments/checkout'));
    expect(call?.body).toMatchObject({ plan_code: 'pro_monthly', provider: 'stars' });
    await waitFor(() => expect(useUi.getState().toasts.at(-1)?.kind).toBe('success'));
  });

  it('sends the user to the gateway page for a redirect provider', async () => {
    const openLink = vi.fn();
    vi.stubGlobal('Telegram', { WebApp: { initData: 'x', openLink } });
    mockApi(
      routes({
        'POST /v1/payments/checkout': {
          payment_id: 'p-2',
          provider: 'zarinpal',
          amount: 1490000,
          currency: 'IRR',
          kind: 'redirect',
          url: 'https://sandbox.zarinpal.com/pg/StartPay/A1',
          payload: null,
        },
      }),
    );
    renderApp(<Plans />);

    await userEvent.click(await screen.findByText('زرین‌پال'));
    await waitFor(() =>
      expect(openLink).toHaveBeenCalledWith('https://sandbox.zarinpal.com/pg/StartPay/A1'),
    );
  });

  it('shows the card details instead of leaving the app for card transfers', async () => {
    mockApi(
      routes({
        'POST /v1/payments/checkout': {
          payment_id: 'p-3',
          provider: 'card2card',
          amount: 1490000,
          currency: 'IRR',
          kind: 'instructions',
          url: null,
          payload: { card_number: '6037-9900-0000-0000', holder_name: 'علی رضایی', reference: 'ab12cd34' },
        },
      }),
    );
    renderApp(<Plans />);

    await userEvent.click(await screen.findByText('کارت به کارت'));
    expect(await screen.findByText('6037-9900-0000-0000')).toBeInTheDocument();
    expect(screen.getByText('علی رضایی')).toBeInTheDocument();
    expect(screen.getByText(/ab12cd34/)).toBeInTheDocument();
  });

  it('validates a discount code on the server before showing a new price', async () => {
    mockApi(
      routes({
        'GET /v1/payments/discount': {
          valid: true,
          amount: 1192000,
          original_amount: 1490000,
          discount_amount: 298000,
          currency: 'IRR',
          reason: null,
        },
      }),
    );
    renderApp(<Plans />);

    await userEvent.type(await screen.findByPlaceholderText('کد تخفیف'), 'OFF20');
    await userEvent.click(screen.getByText('اعمال'));
    expect(await screen.findByText(/۱۱۹٬۲۰۰|119,200/)).toBeInTheDocument();
  });

  it('reports an invalid discount code without changing the price', async () => {
    mockApi(
      routes({
        'GET /v1/payments/discount': {
          valid: false,
          amount: 1490000,
          original_amount: 1490000,
          discount_amount: 0,
          currency: 'IRR',
          reason: 'invalid_code',
        },
      }),
    );
    renderApp(<Plans />);

    await userEvent.type(await screen.findByPlaceholderText('کد تخفیف'), 'NOPE');
    await userEvent.click(screen.getByText('اعمال'));
    await waitFor(() => expect(useUi.getState().toasts.at(-1)?.kind).toBe('error'));
  });

  it('starts the free trial and then hides the offer', async () => {
    mockApi(
      routes({
        'POST /v1/payments/trial': {
          plan_code: 'pro_monthly',
          status: 'trialing',
          source: 'trial',
          started_at: '2026-09-18T00:00:00Z',
          expires_at: '2026-09-25T00:00:00Z',
          grace_until: null,
          days_left: 7,
          auto_renew: false,
        },
      }),
    );
    renderApp(<Plans />);

    await userEvent.click(await screen.findByText(/رایگان امتحان کن/));
    await waitFor(() => expect(useUi.getState().toasts.at(-1)?.kind).toBe('success'));
    expect(await screen.findByText('دورهٔ آزمایشی')).toBeInTheDocument();
  });

  it('shows the expiry date for an active subscription', async () => {
    mockApi(
      routes({
        'GET /v1/me/subscription': {
          plan_code: 'pro_monthly',
          status: 'active',
          source: 'payment',
          started_at: '2026-09-01T00:00:00Z',
          expires_at: '2026-10-01T00:00:00Z',
          grace_until: null,
          days_left: 13,
          auto_renew: false,
        },
      }),
    );
    renderApp(<Plans />);
    expect(await screen.findByText('اشتراک فعال')).toBeInTheDocument();
  });

  it('warns during the grace period', async () => {
    mockApi(
      routes({
        'GET /v1/me/subscription': {
          plan_code: 'pro_monthly',
          status: 'grace',
          source: 'payment',
          started_at: '2026-08-01T00:00:00Z',
          expires_at: '2026-09-01T00:00:00Z',
          grace_until: '2026-09-04T00:00:00Z',
          days_left: 0,
          auto_renew: false,
        },
      }),
    );
    renderApp(<Plans />);
    expect(await screen.findByText(/مهلت داری تمدید کنی/)).toBeInTheDocument();
  });

  it('says so when no gateway is enabled', async () => {
    mockApi(routes({ 'GET /v1/plans': { ...plans, providers: [], trial_available: false } }));
    renderApp(<Plans />);
    expect(await screen.findByText('فعلاً روش پرداختی فعال نیست.')).toBeInTheDocument();
  });

  it('reports a failed checkout instead of leaving the user waiting', async () => {
    mockApi(
      routes({
        'POST /v1/payments/checkout': new Response(
          JSON.stringify({ error: { code: 'unavailable', message: 'gateway down' } }),
          { status: 503 },
        ),
      }),
    );
    renderApp(<Plans />);
    await userEvent.click(await screen.findByText('زرین‌پال'));
    await waitFor(() => expect(useUi.getState().toasts.at(-1)?.kind).toBe('error'));
  });
});
