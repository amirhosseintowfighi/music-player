import { useNavigate } from 'react-router-dom';

import { usePack } from '@/i18n/pack';
import { extrasStrings } from '@/i18n/packs/extras';
import { haptic } from '@/lib/telegram';

type Extra = { key: keyof typeof extrasStrings.fa; glyph: string; to: string };

const EXTRAS: Extra[] = [{ key: 'extras.quiz', glyph: '🎮', to: '/quiz' }];

/** The things to do besides listening: one row of chips on Home. */
export function ExtrasRow() {
  const tp = usePack(extrasStrings);
  const navigate = useNavigate();
  return (
    <div className="tv-row no-scrollbar -mx-4 mb-2 flex gap-2 overflow-x-auto px-4 pb-1" data-tour="extras">
      {EXTRAS.map((extra) => (
        <button data-focusable
          key={extra.to}
          type="button"
          onClick={() => {
            haptic('select');
            navigate(extra.to);
          }}
          className="flex shrink-0 items-center gap-1.5 rounded-full bg-[var(--fill)] px-3.5 py-2 text-[12.5px] font-semibold transition-transform active:scale-95"
        >
          <span aria-hidden>{extra.glyph}</span>
          {tp(extra.key)}
        </button>
      ))}
    </div>
  );
}
