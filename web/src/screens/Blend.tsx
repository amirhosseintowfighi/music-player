/**
 * Blend: one playlist made from two people's tastes.
 *
 * `/blend` lists your Blends and makes an invite; `/blend/:code` is where the invite
 * lands (`startapp=bl_<code>`) and makes the Blend with whoever sent it.
 */
import { animate, motion, useMotionValue, useTransform } from 'framer-motion';
import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { ApiError } from '@/api/client';
import { useMe } from '@/api/hooks';
import { useBlendActions, useBlends, type Blend } from '@/api/listening';
import { BlendIcon, ChevronIcon, SendIcon } from '@/components/icons';
import { EmptyState, Spinner, bouncy, easeOut } from '@/components/ui';
import { useI18n } from '@/i18n';
import { coverColors } from '@/lib/format';
import { haptic, openTelegramLink } from '@/lib/telegram';
import { useUi } from '@/store/ui';

function Initial({ name, seed, size = 44 }: { name: string; seed: number; size?: number }) {
  const [c1, c2] = coverColors(seed);
  return (
    <span
      className="grid shrink-0 place-items-center rounded-full font-bold text-white ring-2 ring-[var(--bg-0)]"
      style={{ width: size, height: size, background: `linear-gradient(140deg, ${c1}, ${c2})`, fontSize: size * 0.4 }}
    >
      {[...name.trim()][0]?.toUpperCase() ?? '♪'}
    </span>
  );
}

/** The taste match as a ring that fills up, with the number counting along. */
function MatchRing({ percent, size = 54 }: { percent: number; size?: number }) {
  const progress = useMotionValue(0);
  const shown = useTransform(progress, (value) => Math.round(value));
  const [label, setLabel] = useState(0);
  useEffect(() => {
    const controls = animate(progress, percent, { duration: 1.1, ease: easeOut });
    const stop = shown.on('change', setLabel);
    return () => {
      controls.stop();
      stop();
    };
  }, [percent, progress, shown]);
  const r = size / 2 - 4;
  const circumference = 2 * Math.PI * r;
  return (
    <div className="relative grid shrink-0 place-items-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--fill-strong)" strokeWidth={4} />
        <motion.circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="var(--accent)"
          strokeWidth={4}
          strokeLinecap="round"
          strokeDasharray={circumference}
          initial={{ strokeDashoffset: circumference }}
          animate={{ strokeDashoffset: circumference * (1 - percent / 100) }}
          transition={{ duration: 1.1, ease: easeOut }}
        />
      </svg>
      <span className="absolute text-[12px] font-bold">{label}%</span>
    </div>
  );
}

function BlendCard({ blend, myName, myId }: { blend: Blend; myName: string; myId: number }) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { refresh, leave } = useBlendActions();
  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.95 }}
      className="rounded-[var(--radius-glass)] bg-[var(--card)] p-3.5"
    >
      <button
        type="button"
        className="flex w-full items-center gap-3 text-start"
        disabled={!blend.playlist_id}
        onClick={() => blend.playlist_id && navigate(`/playlist/${blend.playlist_id}`)}
      >
        <span className="flex shrink-0 -space-x-3 rtl:space-x-reverse">
          <Initial name={myName} seed={myId} />
          <Initial name={blend.other_name} seed={blend.other_user_id} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[15px] font-bold">{t('blend.pair', { name: blend.other_name })}</span>
          <span className="block text-[12px] text-[var(--ink-dim)]">{t('blend.match')}</span>
        </span>
        <MatchRing percent={blend.match_pct} />
      </button>
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          className="flex-1 rounded-xl bg-[var(--fill)] py-2 text-[12.5px]"
          disabled={refresh.isPending}
          onClick={() => refresh.mutate(blend.id)}
        >
          {refresh.isPending ? <Spinner size={14} /> : t('blend.refresh')}
        </button>
        <button
          type="button"
          className="flex-1 rounded-xl bg-[var(--fill)] py-2 text-[12.5px] text-[#ff453a]"
          onClick={() => leave.mutate(blend.id)}
        >
          {t('blend.leave')}
        </button>
      </div>
    </motion.div>
  );
}

export function BlendScreen() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { code } = useParams<{ code?: string }>();
  const me = useMe();
  const blends = useBlends();
  const { invite, join } = useBlendActions();
  const toast = useUi((s) => s.toast);
  const [failed, setFailed] = useState<string | null>(null);
  const tried = useRef<string | null>(null);

  // An invite link: make the Blend, then show it.
  useEffect(() => {
    if (!code || tried.current === code) return;
    tried.current = code;
    join.mutate(code, {
      onSuccess: () => {
        haptic('success');
        navigate('/blend', { replace: true });
      },
      onError: (error) =>
        setFailed(error instanceof ApiError && error.status === 422 ? t('blend.self') : t('blend.expired')),
    });
  }, [code, join, navigate, t]);

  if (code && failed) return <EmptyState title={failed} cta={t('blend.title')} onCta={() => navigate('/blend', { replace: true })} />;
  if (code) {
    return (
      <div className="grid place-items-center py-24">
        <Spinner />
        <p className="mt-3 text-[13px] text-[var(--ink-dim)]">{t('blend.joining')}</p>
      </div>
    );
  }

  const sendInvite = () =>
    invite.mutate(undefined, {
      onSuccess: (made) => {
        openTelegramLink(
          `https://t.me/share/url?url=${encodeURIComponent(made.share_url)}&text=${encodeURIComponent(t('blend.shareText'))}`,
        );
      },
      onError: () => toast(t('app.error'), 'error'),
    });

  return (
    <div className="px-4 pt-4">
      <div className="flex items-center gap-2">
        <button type="button" aria-label={t('common.back')} onClick={() => navigate(-1)} className="p-1.5">
          <ChevronIcon size={22} className="rtl:rotate-180" />
        </button>
        <h1 className="text-[24px] font-bold tracking-tight">{t('blend.title')}</h1>
      </div>

      <div className="relative mt-3 overflow-hidden rounded-[var(--radius-glass-lg)] bg-[var(--card)] p-5 text-center">
        <div className="relative mx-auto mb-3 h-20 w-32">
          <motion.span
            className="absolute left-2 top-0 h-20 w-20 rounded-full bg-[var(--accent)] opacity-80 mix-blend-screen"
            animate={{ x: [0, 10, 0] }}
            transition={{ duration: 3.2, repeat: Infinity, ease: 'easeInOut' }}
          />
          <motion.span
            className="absolute right-2 top-0 h-20 w-20 rounded-full bg-[#5e5ce6] opacity-80 mix-blend-screen"
            animate={{ x: [0, -10, 0] }}
            transition={{ duration: 3.2, repeat: Infinity, ease: 'easeInOut' }}
          />
          <BlendIcon size={30} className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 text-white" />
        </div>
        <p className="text-[15px] font-bold">{t('blend.headline')}</p>
        <p className="mx-auto mt-1 max-w-xs text-[12.5px] leading-6 text-[var(--ink-dim)]">{t('blend.hint')}</p>
        <motion.button
          type="button"
          whileTap={{ scale: 0.95 }}
          transition={bouncy}
          disabled={invite.isPending}
          onClick={sendInvite}
          className="mt-4 inline-flex items-center gap-2 rounded-full bg-[var(--accent)] px-5 py-2.5 text-[14px] font-bold text-[var(--accent-ink)] disabled:opacity-60"
        >
          {invite.isPending ? <Spinner size={16} /> : <SendIcon size={16} />}
          {t('blend.invite')}
        </motion.button>
      </div>

      <div className="mt-5 flex flex-col gap-3">
        {blends.isLoading && (
          <div className="grid place-items-center py-6">
            <Spinner />
          </div>
        )}
        {blends.data?.map((blend) => (
          <BlendCard key={blend.id} blend={blend} myName={me.data?.first_name ?? '♪'} myId={me.data?.id ?? 0} />
        ))}
      </div>
    </div>
  );
}
