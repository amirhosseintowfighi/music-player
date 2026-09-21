import { useMemo, useState } from 'react';

import {
  useCheckout,
  useDiscountPreview,
  usePlans,
  useRefreshBilling,
  useStartTrial,
  useSubscription,
  type Plan,
  type Provider,
} from '@/api/billing';
import { ApiError } from '@/api/client';
import { Glass, Sheet, Spinner, cx } from '@/components/ui';
import { useI18n } from '@/i18n';
import { formatDate } from '@/lib/format';
import { haptic, openExternalLink, openInvoice } from '@/lib/telegram';
import { useUi } from '@/store/ui';

const PROVIDER_ICON: Record<string, string> = {
  stars: '⭐',
  zarinpal: '🔵',
  idpay: '🟣',
  nextpay: '🟢',
  card2card: '💳',
};

const PROVIDER_KEY = {
  stars: 'plan.provider.stars',
  zarinpal: 'plan.provider.zarinpal',
  idpay: 'plan.provider.idpay',
  nextpay: 'plan.provider.nextpay',
  card2card: 'plan.provider.card2card',
} as const;

function price(plan: Plan, currency: string, lang: string): string {
  const amount = plan.prices[currency] ?? 0;
  if (currency === 'XTR') return `${amount} ⭐`;
  // Prices are stored in rial; Iranians read toman.
  return `${Math.round(amount / 10).toLocaleString(lang === 'fa' ? 'fa-IR' : 'en-US')}`;
}

function PlanCard({
  plan,
  currency,
  selected,
  onSelect,
}: {
  plan: Plan;
  currency: string;
  selected: boolean;
  onSelect: () => void;
}) {
  const { t, lang } = useI18n();
  const perMonth = plan.period_days && plan.period_days > 31;
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cx(
        'glass glass-edge w-full rounded-[var(--radius-glass)] p-4 text-start transition-colors',
        selected && 'ring-2 ring-[var(--accent)]',
      )}
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[15px] font-bold">{plan.name}</span>
        <span className="text-[15px] font-bold text-[var(--accent)]">
          {price(plan, currency, lang)}
          {currency !== 'XTR' && (
            <span className="ms-1 text-[11px] font-normal text-[var(--ink-faint)]">
              {t('plan.toman')}
            </span>
          )}
        </span>
      </div>
      <p className="mt-1 text-[11.5px] text-[var(--ink-faint)]">
        {plan.period_days === 365 ? t('plan.yearly') : t('plan.monthly')}
        {perMonth && ` · ${t('plan.saving')}`}
      </p>
    </button>
  );
}

function Features({ plan }: { plan: Plan }) {
  const { t } = useI18n();
  const items = [
    t('plan.feature.channels'),
    t('plan.feature.playlists'),
    t('plan.feature.plays'),
    ...(plan.features.includes('offline') ? [t('plan.feature.offline')] : []),
    ...(plan.features.includes('share_playlist') ? [t('plan.feature.share')] : []),
  ];
  return (
    <ul className="mt-4 space-y-2">
      {items.map((item) => (
        <li key={item} className="flex items-center gap-2 text-[13px] text-[var(--ink-dim)]">
          <span className="text-[var(--accent)]">✓</span>
          {item}
        </li>
      ))}
    </ul>
  );
}

export function Plans() {
  const { t, lang } = useI18n();
  const toast = useUi((s) => s.toast);
  const plans = usePlans();
  const subscription = useSubscription();
  const checkout = useCheckout();
  const trial = useStartTrial();
  const discount = useDiscountPreview();
  const refresh = useRefreshBilling();

  const [planCode, setPlanCode] = useState<string | null>(null);
  const [code, setCode] = useState('');
  const [applied, setApplied] = useState<number | null>(null);
  const [instructions, setInstructions] = useState<Record<string, unknown> | null>(null);

  const paid = useMemo(
    () => (plans.data?.plans ?? []).filter((plan) => plan.period_days !== null),
    [plans.data],
  );
  const selected = paid.find((plan) => plan.code === planCode) ?? paid[0];
  const providers = plans.data?.providers ?? [];

  if (plans.isLoading || subscription.isLoading) {
    return (
      <div className="flex justify-center py-16">
        <Spinner />
      </div>
    );
  }

  const live = subscription.data;
  const isPro = live && live.status !== 'free' && live.status !== 'expired';

  async function buy(provider: Provider): Promise<void> {
    if (!selected) return;
    haptic('medium');
    try {
      const result = await checkout.mutateAsync({
        plan_code: selected.code,
        provider: provider.code,
        discount_code: applied !== null && code ? code : null,
      });
      if (result.kind === 'telegram_invoice' && result.url) {
        const status = await openInvoice(result.url);
        if (status === 'paid') {
          toast(t('plan.paid'), 'success');
          refresh();
        } else if (status === 'failed') {
          toast(t('plan.failed'), 'error');
        }
        return;
      }
      if (result.kind === 'redirect' && result.url) {
        openExternalLink(result.url);
        return;
      }
      if (result.kind === 'instructions') {
        setInstructions(result.payload ?? {});
      }
    } catch (error) {
      const known = error instanceof ApiError && error.code === 'invalid_input';
      toast(known ? t('plan.codeInvalid') : t('plan.failed'), 'error');
    }
  }

  async function applyCode(): Promise<void> {
    if (!selected || !code.trim()) return;
    const preview = await discount.mutateAsync({
      code: code.trim(),
      plan_code: selected.code,
      currency: 'IRR',
    });
    if (preview.valid) {
      setApplied(preview.amount);
      toast(t('plan.codeApplied'), 'success');
    } else {
      setApplied(null);
      toast(t('plan.codeInvalid'), 'error');
    }
  }

  return (
    <div className="space-y-4 px-4 pb-32 pt-3">
      {isPro && live && (
        <Glass className="p-4">
          <p className="text-[13px] text-[var(--ink-dim)]">
            {live.status === 'trialing' ? t('plan.trialActive') : t('plan.active')}
          </p>
          <p className="mt-1 text-[15px] font-bold">
            {t('plan.until', { date: live.expires_at ? formatDate(live.expires_at, lang) : '—' })}
          </p>
          {live.status === 'grace' && (
            <p className="mt-2 text-[12px] text-[#f5a524]">{t('plan.grace')}</p>
          )}
        </Glass>
      )}

      <div>
        <h2 className="mb-1 text-[19px] font-bold">{t('plan.title')}</h2>
        <p className="text-[12.5px] text-[var(--ink-faint)]">{t('plan.subtitle')}</p>
      </div>

      <div className="grid grid-cols-2 gap-2">
        {paid.map((plan) => (
          <PlanCard
            key={plan.code}
            plan={plan}
            currency="IRR"
            selected={selected?.code === plan.code}
            onSelect={() => setPlanCode(plan.code)}
          />
        ))}
      </div>

      {selected && <Features plan={selected} />}

      {plans.data?.trial_available && (
        <button
          type="button"
          onClick={() => {
            trial.mutate(undefined, {
              onSuccess: () => toast(t('plan.trialStarted'), 'success'),
              onError: () => toast(t('plan.failed'), 'error'),
            });
          }}
          disabled={trial.isPending}
          className="w-full rounded-xl border border-[var(--accent)]/40 py-2.5 text-[13.5px] text-[var(--accent)]"
        >
          {t('plan.startTrial', { days: plans.data.trial_days })}
        </button>
      )}

      <div className="flex gap-2">
        <input
          value={code}
          onChange={(event) => setCode(event.target.value)}
          placeholder={t('plan.codePlaceholder')}
          className="glass min-w-0 flex-1 rounded-xl px-3 py-2.5 text-[13px] text-[var(--ink)] outline-none"
        />
        <button
          type="button"
          onClick={() => void applyCode()}
          disabled={discount.isPending || !code.trim()}
          className="glass rounded-xl px-4 text-[13px]"
        >
          {t('plan.applyCode')}
        </button>
      </div>
      {applied !== null && (
        <p className="text-[12px] text-[var(--accent)]">
          {t('plan.newPrice', { amount: Math.round(applied / 10).toLocaleString(lang === 'fa' ? 'fa-IR' : 'en-US') })}
        </p>
      )}

      <div className="space-y-2">
        <p className="text-[12.5px] text-[var(--ink-faint)]">{t('plan.payWith')}</p>
        {providers.map((provider) => (
          <button
            key={provider.code}
            type="button"
            onClick={() => void buy(provider)}
            disabled={checkout.isPending}
            className="glass glass-edge flex w-full items-center gap-3 rounded-xl px-3.5 py-3 text-start text-[13.5px]"
          >
            <span aria-hidden>{PROVIDER_ICON[provider.code] ?? '💠'}</span>
            <span className="flex-1">
              {provider.code in PROVIDER_KEY
                ? t(PROVIDER_KEY[provider.code as keyof typeof PROVIDER_KEY])
                : provider.code}
            </span>
            {checkout.isPending ? <Spinner size={14} /> : <span aria-hidden>›</span>}
          </button>
        ))}
        {providers.length === 0 && (
          <p className="text-[12.5px] text-[var(--ink-faint)]">{t('plan.noProviders')}</p>
        )}
      </div>

      <Sheet
        open={instructions !== null}
        onClose={() => setInstructions(null)}
        title={t('plan.provider.card2card')}
      >
        <div className="space-y-3 px-1 pb-2 text-[13.5px]">
          <p className="text-[var(--ink-dim)]">{t('plan.cardHowto')}</p>
          <div className="glass rounded-xl p-3 text-center">
            <p className="font-mono text-[17px] tracking-widest">
              {String(instructions?.card_number ?? '—')}
            </p>
            <p className="mt-1 text-[12px] text-[var(--ink-faint)]">
              {String(instructions?.holder_name ?? '')}
            </p>
          </div>
          <p className="text-[12.5px] text-[var(--ink-faint)]">
            {t('plan.cardRef', { ref: String(instructions?.reference ?? '') })}
          </p>
        </div>
      </Sheet>
    </div>
  );
}
