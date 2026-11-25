import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';

import { useAchievements, useMarkBadgesSeen } from '@/api/achievements';
import { ChevronIcon } from '@/components/icons';
import { EmptyState, Glass, Spinner, cx } from '@/components/ui';
import { useI18n } from '@/i18n';
import { usePack } from '@/i18n/pack';
import { badgeStrings } from '@/i18n/packs/badges';
import { formatDate } from '@/lib/format';

type Key = keyof typeof badgeStrings.fa;

const GLYPHS: Record<string, string> = {
  first_play: '🎵',
  streak_3: '🔥',
  streak_7: '📅',
  streak_30: '🌙',
  streak_100: '👑',
  plays_100: '💯',
  plays_1000: '🚀',
  collector: '❤️',
  curator: '📚',
  explorer: '🧭',
  night_owl: '🦉',
  early_bird: '🐦',
  social: '🤝',
  quiz_master: '🎮',
};

export function AchievementsScreen() {
  const { t, lang } = useI18n();
  const tp = usePack(badgeStrings);
  const navigate = useNavigate();
  const data = useAchievements();
  const seen = useMarkBadgesSeen();
  const hasNew = data.data?.badges.some((badge) => badge.new) ?? false;

  // The celebration shows once: looking at the page is what makes them "seen".
  const { mutate } = seen;
  useEffect(() => {
    if (!hasNew) return undefined;
    const timer = window.setTimeout(() => mutate(), 2500);
    return () => window.clearTimeout(timer);
  }, [hasNew, mutate]);

  if (data.isLoading) {
    return (
      <div className="grid place-items-center py-20">
        <Spinner />
      </div>
    );
  }
  if (!data.data) return <EmptyState title={t('app.error')} />;
  const { streak, badges } = data.data;
  const dayNames = lang === 'fa' ? ['ی', 'د', 'س', 'چ', 'پ', 'ج', 'ش'] : ['S', 'M', 'T', 'W', 'T', 'F', 'S'];
  const today = new Date().getDay();

  return (
    <div className="px-4 pb-8 pt-4 tv-safe [container-type:inline-size]">
      <div className="flex items-center gap-2">
        <button data-focusable type="button" aria-label={t('common.back')} onClick={() => navigate(-1)} className="p-1.5">
          <ChevronIcon size={22} className="rtl:rotate-180" />
        </button>
        <h1 className="text-[21px] font-bold">{tp('ach.title')}</h1>
      </div>

      <Glass className="mt-4 p-5 text-center">
        <p className="text-[13px] text-[var(--ink-dim)]">{tp('ach.streak')}</p>
        <p className="mt-1 text-[48px] font-extrabold leading-none">
          <span aria-hidden>🔥</span> {streak.current}
        </p>
        <p className="mt-1 text-[13.5px] font-semibold">{tp('ach.days', { count: streak.current })}</p>
        <div className="mt-4 flex justify-center gap-2" aria-hidden>
          {streak.week.map((played, index) => (
            <div key={index} className="flex flex-col items-center gap-1">
              <span
                className={cx(
                  'grid h-8 w-8 place-items-center rounded-full text-[14px]',
                  played ? 'bg-[var(--accent)] text-[var(--accent-ink)]' : 'bg-[var(--fill)] text-[var(--ink-faint)]',
                )}
              >
                {played ? '✓' : ''}
              </span>
              <span className="text-[10.5px] text-[var(--ink-faint)]">{dayNames[(today - 6 + index + 7) % 7]}</span>
            </div>
          ))}
        </div>
        <p className="mt-3 text-[12.5px] text-[var(--ink-dim)]">
          {streak.current === 0 ? tp('ach.zero') : streak.today_done ? tp('ach.todayDone') : tp('ach.todayPending')}
        </p>
        <p className="mt-1 text-[11.5px] text-[var(--ink-faint)]">{tp('ach.longest', { count: streak.longest })}</p>
      </Glass>

      <h2 className="mt-6 text-[16px] font-bold">{tp('ach.badges', { earned: data.data.earned, total: data.data.total })}</h2>
      <div className="mt-3 grid grid-cols-2 gap-2">
        {badges.map((badge) => {
          const name = `badge.${badge.code}` as Key;
          const ratio = badge.goal > 0 ? badge.progress / badge.goal : 0;
          return (
            <Glass key={badge.code} className={cx('relative flex flex-col gap-1.5 p-3', !badge.earned && 'opacity-60')}>
              {badge.new && (
                <span className="absolute end-2 top-2 rounded-full bg-[var(--accent)] px-2 py-0.5 text-[10px] font-bold text-[var(--accent-ink)]">
                  {tp('ach.new')}
                </span>
              )}
              <span className={cx('text-[30px]', !badge.earned && 'grayscale')} aria-hidden>
                {GLYPHS[badge.code] ?? '🏅'}
              </span>
              <span className="text-[13.5px] font-bold">{tp(name)}</span>
              <span className="text-[11.5px] leading-4 text-[var(--ink-dim)]">{tp(`${name}.d` as Key)}</span>
              {badge.earned && badge.earned_at ? (
                <span className="text-[10.5px] text-[var(--ink-faint)]">
                  {tp('ach.earnedOn', { when: formatDate(badge.earned_at, lang) })}
                </span>
              ) : (
                <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-[var(--fill)]" role="progressbar" aria-valuenow={badge.progress} aria-valuemax={badge.goal}>
                  <div className="h-full bg-[var(--accent)]" style={{ width: `${Math.round(ratio * 100)}%` }} />
                </div>
              )}
            </Glass>
          );
        })}
      </div>
    </div>
  );
}
