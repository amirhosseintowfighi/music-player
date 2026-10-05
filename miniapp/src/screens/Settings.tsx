import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { useMe, useSetLanguage } from '@/api/hooks';
import {
  useNotificationPrefs,
  useSetNotificationPrefs,
  useSetPublicProfile,
} from '@/api/social';
import { usePrivateSession, useSetPrivateSession } from '@/api/listening';
import { JamIcon } from '@/components/icons';
import { Credit, Glass, cx } from '@/components/ui';
import { useI18n, type Key } from '@/i18n';
import { EQ_PRESETS, useAudioSettings, type EqPreset } from '@/store/audio';
import { useUi } from '@/store/ui';
import { resetTour } from '@/lib/tour';

function Row({ label, hint, children, labelId }: { label: string; hint?: string; children: React.ReactNode; labelId?: string }) {
  return (
    <div className="flex items-center justify-between gap-3 px-3.5 py-3">
      <div className="min-w-0">
        <p id={labelId} className="text-[14px]">{label}</p>
        {hint && <p className="mt-0.5 text-[11.5px] text-[var(--ink-faint)]">{hint}</p>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

/** A plain on/off switch; the panel has enough chrome already. */
function Toggle({ on, onChange, label, labelId }: { on: boolean; onChange: (next: boolean) => void; label?: string; labelId?: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={labelId ? undefined : label}
      aria-labelledby={labelId}
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
  'new_release',
] as const;

const NOTIFY_LABEL = {
  new_tracks: 'notify.new_tracks',
  digest: 'notify.digest',
  discover_ready: 'notify.discover_ready',
  sub_expiry: 'notify.sub_expiry',
  payment: 'notify.payment',
  system: 'notify.system',
  new_release: 'notify.new_release',
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

const EQ_LABEL: Record<EqPreset, Key> = {
  flat: 'audio.eq.flat',
  bass: 'audio.eq.bass',
  treble: 'audio.eq.treble',
  vocal: 'audio.eq.vocal',
  acoustic: 'audio.eq.acoustic',
  electronic: 'audio.eq.electronic',
  night: 'audio.eq.night',
};

/** How the music sounds on this device (Music's "Playback" settings). */
function PlaybackSettings() {
  const { t, n } = useI18n();
  const audio = useAudioSettings();
  const privateSession = usePrivateSession();
  const setPrivate = useSetPrivateSession();
  const privateOn = Boolean(privateSession.data?.private_until);
  return (
    <>
      <h2 className="mb-2 mt-5 px-1 text-[13px] font-semibold text-[var(--ink-dim)]">{t('audio.title')}</h2>
      <Glass className="mb-3 divide-y divide-[var(--separator)]">
        <div className="px-3.5 py-3">
          <div className="flex items-center justify-between">
            <p className="text-[14px]">{t('audio.crossfade')}</p>
            <span className="text-[13px] text-[var(--ink-dim)]">
              {audio.crossfade === 0 ? t('audio.off') : t('audio.seconds', { count: audio.crossfade })}
            </span>
          </div>
          <input
            type="range"
            min={0}
            max={12}
            step={1}
            value={audio.crossfade}
            aria-label={t('audio.crossfade')}
            onChange={(event) => audio.set({ crossfade: Number(event.target.value) })}
            className="mt-2 w-full accent-[var(--accent)]"
          />
          <p className="mt-0.5 text-[11.5px] text-[var(--ink-faint)]">{t('audio.crossfade.hint', { max: n(12) })}</p>
        </div>
        <Row label={t('audio.gapless')} hint={t('audio.gapless.hint')}>
          <Toggle on={audio.gapless} onChange={(gapless) => audio.set({ gapless })} label={t('audio.gapless')} />
        </Row>
        <Row label={t('audio.normalize')} hint={t('audio.normalize.hint')}>
          <Toggle on={audio.normalize} onChange={(normalize) => audio.set({ normalize })} label={t('audio.normalize')} />
        </Row>
        <div className="px-3.5 py-3">
          <p className="text-[14px]">{t('audio.eq')}</p>
          <p className="mt-0.5 text-[11.5px] text-[var(--ink-faint)]">{t('audio.eq.hint')}</p>
          <div className="no-scrollbar -mx-1 mt-2.5 flex gap-2 overflow-x-auto px-1">
            {(Object.keys(EQ_PRESETS) as EqPreset[]).map((preset) => (
              <button
                key={preset}
                type="button"
                aria-pressed={audio.eq === preset}
                onClick={() => audio.set({ eq: preset })}
                className={cx(
                  'shrink-0 rounded-full px-3 py-1.5 text-[12px] transition-colors',
                  audio.eq === preset
                    ? 'bg-[var(--accent)] font-bold text-[var(--accent-ink)]'
                    : 'bg-[var(--fill)] text-[var(--ink-dim)]',
                )}
              >
                {t(EQ_LABEL[preset])}
              </button>
            ))}
          </div>
        </div>
        <Row label={t('audio.canvas')} hint={t('audio.canvas.hint')}>
          <Toggle on={audio.canvas} onChange={(canvas) => audio.set({ canvas })} label={t('audio.canvas')} />
        </Row>
        <Row label={t('private.title')} hint={t('private.hint')}>
          <Toggle on={privateOn} onChange={(on) => setPrivate.mutate(on)} label={t('private.title')} />
        </Row>
      </Glass>
    </>
  );
}

export function Settings() {
  const navigate = useNavigate();
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

      <a
        href="#/jam"
        className="mb-3 flex items-center gap-3 rounded-[var(--radius-glass)] bg-[var(--card)] px-3.5 py-3 transition-transform active:scale-[0.98]"
      >
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-gradient-to-br from-[var(--accent)] to-[var(--accent-2)] text-[var(--accent-ink)]">
          <JamIcon size={22} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-[14px] font-semibold">{t('jam.title')}</span>
          <span className="block truncate text-[11.5px] text-[var(--ink-faint)]">{t('jam.startTitle')}</span>
        </span>
      </a>

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
            label={t('settings.privacy')}
          />
        </Row>
        {NOTIFY_KEYS.map((key) => (
          <Row key={key} label={t(NOTIFY_LABEL[key])}>
            <Toggle
              on={notify.data?.prefs[key] ?? true}
              onChange={(next) => setNotify.mutate({ [key]: next })}
              label={t(NOTIFY_LABEL[key])}
            />
          </Row>
        ))}
      </Glass>

      <PlaybackSettings />

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
      <Glass className="mt-4">
        <Row label={t('tour.replay')} hint={t('tour.replay.hint')}>
          <button
            type="button"
            className="rounded-full bg-[var(--fill)] px-3 py-1.5 text-[12px]"
            onClick={() => {
              resetTour();
              useUi.getState().toast(t('tour.replayed'), 'success');
              navigate('/');
              setTimeout(() => window.location.reload(), 300);
            }}
          >
            {t('tour.replay')}
          </button>
        </Row>
      </Glass>
      <Credit />
    </div>
  );
}
