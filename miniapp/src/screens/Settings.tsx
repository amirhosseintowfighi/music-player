import { useState } from 'react';

import { useMe, useSetLanguage } from '@/api/hooks';
import {
  useNotificationPrefs,
  useSetNotificationPrefs,
  useSetPublicProfile,
} from '@/api/social';
import { Credit, Glass, cx } from '@/components/ui';
import { useI18n } from '@/i18n';
import { useUi } from '@/store/ui';

function Row({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 px-3.5 py-3">
      <div className="min-w-0">
        <p className="text-[14px]">{label}</p>
        {hint && <p className="mt-0.5 text-[11.5px] text-[var(--ink-faint)]">{hint}</p>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

/** A plain on/off switch; the panel has enough chrome already. */
function Toggle({ on, onChange }: { on: boolean; onChange: (next: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      onClick={() => onChange(!on)}
      className={cx(
        'h-6 w-11 rounded-full p-0.5 transition-colors',
        on ? 'bg-[var(--accent)]' : 'bg-[var(--fill-strong)]',
      )}
    >
      <span
        className={cx(
          'block h-5 w-5 rounded-full bg-white transition-transform',
          on ? 'translate-x-0' : 'translate-x-5 rtl:-translate-x-5',
        )}
      />
    </button>
  );
}

const NOTIFY_KEYS = [
  'new_tracks',
  'digest',
  'discover_ready',
  'sub_expiry',
  'payment',
  'system',
] as const;

const NOTIFY_LABEL = {
  new_tracks: 'notify.new_tracks',
  digest: 'notify.digest',
  discover_ready: 'notify.discover_ready',
  sub_expiry: 'notify.sub_expiry',
  payment: 'notify.payment',
  system: 'notify.system',
} as const;

function Choice<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: { id: T; label: string }[];
  onChange: (value: T) => void;
}) {
  return (
    <div className="flex gap-1 rounded-full bg-[var(--fill)] p-1">
      {options.map((option) => (
        <button
          key={option.id}
          type="button"
          onClick={() => onChange(option.id)}
          className={cx(
            'rounded-full px-3 py-1 text-[12px]',
            value === option.id ? 'bg-[var(--accent)] font-bold text-[var(--accent-ink)]' : 'text-[var(--ink-dim)]',
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function Settings() {
  const { t } = useI18n();
  const me = useMe();
  const setLanguage = useSetLanguage();
  const ui = useUi();
  const [copied, setCopied] = useState(false);
  const setPublic = useSetPublicProfile();
  const notify = useNotificationPrefs();
  const setNotify = useSetNotificationPrefs();

  const plan = me.data?.plan ?? 'free';

  return (
    <div className="px-4 pt-4">
      <h1 className="mb-4 text-[21px] font-bold">{t('settings.title')}</h1>

      <Glass className="mb-3 divide-y divide-white/6">
        <Row label={t('settings.language')}>
          <Choice
            value={ui.lang}
            options={[
              { id: 'fa', label: 'فارسی' },
              { id: 'en', label: 'English' },
            ]}
            onChange={(lang) => {
              ui.setLang(lang);
              setLanguage.mutate(lang);
            }}
          />
        </Row>
        <Row label={t('settings.theme')}>
          <Choice
            value={ui.theme}
            options={[
              { id: 'dark', label: t('settings.theme.dark') },
              { id: 'light', label: t('settings.theme.light') },
            ]}
            onChange={ui.setTheme}
          />
        </Row>
        <Row label={t('settings.glass')} hint={t('settings.glass.hint')}>
          <Choice
            value={ui.perf}
            options={[
              { id: 'auto', label: 'auto' },
              { id: 'high', label: 'on' },
              { id: 'low', label: 'off' },
            ]}
            onChange={ui.setPerf}
          />
        </Row>
      </Glass>

      <Glass className="mb-3 divide-y divide-white/6">
        <Row label={t('settings.privacy')} hint={t('settings.privacy.hint')}>
          <Toggle
            on={me.data?.public_profile ?? true}
            onChange={(next) => setPublic.mutate(next)}
          />
        </Row>
        {NOTIFY_KEYS.map((key) => (
          <Row key={key} label={t(NOTIFY_LABEL[key])}>
            <Toggle
              on={notify.data?.prefs[key] ?? true}
              onChange={(next) => setNotify.mutate({ [key]: next })}
            />
          </Row>
        ))}
      </Glass>

      <Glass className="divide-y divide-white/6">
        <Row label={t('profile.plays')}>
          <a href="#/profile" className="rounded-full bg-[var(--fill)] px-3 py-1.5 text-[12px]">
            {t('tab.profile')}
          </a>
        </Row>
        <Row label={t('settings.subscription')}>
          <a
            href="#/plans"
            className={cx(
              'rounded-full px-3 py-1 text-[12px] font-bold',
              plan === 'free' ? 'bg-[var(--fill)]' : 'bg-[var(--accent)] text-[var(--accent-ink)]',
            )}
          >
            {plan === 'free' ? t('plan.free') : t('plan.pro')}
          </a>
        </Row>
        <Row label={t('plan.manage')}>
          <a href="#/plans" className="rounded-full bg-[var(--fill)] px-3 py-1.5 text-[12px]">
            {t('common.more')}
          </a>
        </Row>
        {me.data && (
          <Row label={t('settings.referral')} hint={me.data.referral_code}>
            <button
              type="button"
              className="rounded-full bg-[var(--fill)] px-3 py-1.5 text-[12px]"
              onClick={() => {
                void navigator.clipboard?.writeText(me.data.referral_code);
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              }}
            >
              {copied ? t('settings.copied') : t('settings.copy')}
            </button>
          </Row>
        )}
      </Glass>
      <Credit />
    </div>
  );
}
