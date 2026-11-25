import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { ApiError, type Artist } from '@/api/client';
import { useArtistSearch, useMyChannels } from '@/api/hooks';
import {
  DEFAULT_RULES,
  useCreateSmartPlaylist,
  useSmartPreview,
  useUpdateSmartRules,
  type SmartRules,
} from '@/api/smart';
import { CloseIcon } from '@/components/icons';
import { Sheet, Spinner, cx } from '@/components/ui';
import { usePack } from '@/i18n/pack';
import { smartStrings } from '@/i18n/packs/smart';
import { useI18n } from '@/i18n';
import { haptic } from '@/lib/telegram';
import { useUi } from '@/store/ui';

function Pill({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={() => {
        haptic('select');
        onClick();
      }}
      className={cx(
        'shrink-0 rounded-full px-3 py-1.5 text-[12.5px] transition-colors duration-200',
        on ? 'bg-[var(--accent)] font-bold text-[var(--accent-ink)]' : 'bg-[var(--fill)] text-[var(--ink-dim)]',
      )}
    >
      {children}
    </button>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mt-4">
      <p className="mb-1.5 text-[12px] font-semibold text-[var(--ink-faint)]">{label}</p>
      <div className="no-scrollbar -mx-1 flex flex-wrap gap-1.5 px-1">{children}</div>
    </div>
  );
}

/** A choice among fixed values, "any" included: one row of pills. */
function Choice<T extends string | number>({
  value,
  options,
  onChange,
  label,
}: {
  value: T | null | undefined;
  options: { value: T | null; label: string }[];
  onChange: (value: T | null) => void;
  label: string;
}) {
  return (
    <Field label={label}>
      {options.map((option) => (
        <Pill key={String(option.value)} on={(value ?? null) === option.value} onClick={() => onChange(option.value)}>
          {option.label}
        </Pill>
      ))}
    </Field>
  );
}

function ArtistPicker({ picked, onChange }: { picked: Artist[]; onChange: (next: Artist[]) => void }) {
  const { t } = useI18n();
  const tp = usePack(smartStrings);
  const [query, setQuery] = useState('');
  const [debounced, setDebounced] = useState('');
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(query.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [query]);
  const found = useArtistSearch(debounced);
  const pickedIds = new Set(picked.map((artist) => artist.id));

  return (
    <Field label={tp('smart.artists')}>
      {picked.map((artist) => (
        <span
          key={artist.id}
          className="flex items-center gap-1 rounded-full bg-[var(--accent)] py-1 pe-1.5 ps-3 text-[12.5px] font-bold text-[var(--accent-ink)]"
        >
          {artist.name}
          <button
            type="button"
            aria-label={`${t('common.delete')} ${artist.name}`}
            onClick={() => onChange(picked.filter((one) => one.id !== artist.id))}
          >
            <CloseIcon size={13} />
          </button>
        </span>
      ))}
      {picked.length < 20 && (
        <div className="relative w-full">
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={tp('smart.artistsPlaceholder')}
            aria-label={tp('smart.artists')}
            className="w-full rounded-xl bg-[var(--fill)] px-3.5 py-2 text-[13.5px] outline-none"
          />
          {debounced.length >= 2 && (found.data?.length ?? 0) > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {found.data
                ?.filter((artist) => !pickedIds.has(artist.id))
                .slice(0, 6)
                .map((artist) => (
                  <Pill
                    key={artist.id}
                    on={false}
                    onClick={() => {
                      onChange([...picked, artist]);
                      setQuery('');
                    }}
                  >
                    + {artist.name}
                  </Pill>
                ))}
            </div>
          )}
        </div>
      )}
    </Field>
  );
}

/**
 * Creates a smart playlist, or edits the rules of one (``playlistId``). The count of
 * matching songs updates as the rules change, before anything is saved.
 */
export function SmartPlaylistSheet({
  open,
  onClose,
  playlistId,
  initial,
}: {
  open: boolean;
  onClose: () => void;
  playlistId?: number;
  initial?: SmartRules | null;
}) {
  const { t } = useI18n();
  const tp = usePack(smartStrings);
  const navigate = useNavigate();
  const toast = useUi((s) => s.toast);
  const showUpsell = useUi((s) => s.showUpsell);
  const [name, setName] = useState('');
  const [rules, setRules] = useState<SmartRules>(initial ?? DEFAULT_RULES);
  const [artists, setArtists] = useState<Artist[]>([]);
  const channels = useMyChannels();
  const create = useCreateSmartPlaylist();
  const update = useUpdateSmartRules(playlistId ?? 0);
  const preview = useSmartPreview(rules, open);
  const editing = playlistId !== undefined;

  useEffect(() => {
    if (open) setRules(initial ?? DEFAULT_RULES);
  }, [open, initial]);

  const set = (patch: Partial<SmartRules>) => setRules((current) => ({ ...current, ...patch }));
  const setArtistList = (next: Artist[]) => {
    setArtists(next);
    set({ artist_ids: next.map((artist) => artist.id) });
  };
  const pending = create.isPending || update.isPending;

  const save = () => {
    const onError = (error: unknown) => {
      if (error instanceof ApiError && error.isPlanLimit) {
        onClose();
        showUpsell({ kind: 'playlists', limit: Number(error.details.limit ?? 0) });
      } else {
        toast(t('app.error'), 'error');
      }
    };
    if (editing) {
      update.mutate(rules, {
        onSuccess: () => {
          toast(tp('smart.saved'), 'success');
          onClose();
        },
        onError,
      });
      return;
    }
    create.mutate(
      { name: name.trim(), rules },
      {
        onSuccess: (playlist) => {
          toast(t('playlist.created'), 'success');
          setName('');
          onClose();
          navigate(`/playlist/${playlist.id}`);
        },
        onError,
      },
    );
  };

  const keptArtistIds = new Set(artists.map((artist) => artist.id));
  const unknownArtists = (rules.artist_ids ?? []).filter((id) => !keptArtistIds.has(id)).length;

  return (
    <Sheet open={open} onClose={onClose} title={editing ? tp('smart.editRules') : tp('smart.new')}>
      <p className="text-[12.5px] leading-5 text-[var(--ink-dim)]">{tp('smart.hint')}</p>
      {!editing && (
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder={tp('smart.namePlaceholder')}
          aria-label={t('playlist.name')}
          className="mt-3 w-full rounded-xl bg-[var(--fill)] px-3.5 py-2.5 text-[14px] outline-none"
        />
      )}

      <Choice
        label={tp('smart.source')}
        value={rules.source ?? 'library'}
        onChange={(source) => set({ source: source ?? 'library' })}
        options={[
          { value: 'library', label: tp('smart.source.library') },
          { value: 'likes', label: tp('smart.source.likes') },
          { value: 'catalog', label: tp('smart.source.catalog') },
        ]}
      />

      <ArtistPicker picked={artists} onChange={setArtistList} />
      {unknownArtists > 0 && (
        <p className="mt-1 text-[11.5px] text-[var(--ink-faint)]">
          {tp('smart.artistsKept', { count: unknownArtists })}{' '}
          <button type="button" className="underline" onClick={() => setArtistList([])}>
            {t('library.filter.clear')}
          </button>
        </p>
      )}

      {(channels.data?.length ?? 0) > 0 && (
        <Field label={tp('smart.channels')}>
          {channels.data?.map((channel) => {
            const on = (rules.channel_ids ?? []).includes(channel.id);
            return (
              <Pill
                key={channel.id}
                on={on}
                onClick={() =>
                  set({
                    channel_ids: on
                      ? (rules.channel_ids ?? []).filter((id) => id !== channel.id)
                      : [...(rules.channel_ids ?? []), channel.id].slice(0, 20),
                  })
                }
              >
                {channel.title || channel.username}
              </Pill>
            );
          })}
        </Field>
      )}

      <Choice
        label={t('library.filter.language')}
        value={rules.language ?? null}
        onChange={(language) => set({ language })}
        options={[
          { value: null, label: tp('smart.any') },
          { value: 'fa', label: tp('smart.lang.fa') },
          { value: 'en', label: tp('smart.lang.en') },
          { value: 'ar', label: tp('smart.lang.ar') },
          { value: 'tr', label: tp('smart.lang.tr') },
        ]}
      />

      <Choice
        label={t('library.filter.duration')}
        value={rules.max_duration ? `max${rules.max_duration}` : rules.min_duration ? `min${rules.min_duration}` : null}
        onChange={(choice) =>
          set({
            max_duration: choice?.startsWith('max') ? Number(choice.slice(3)) : null,
            min_duration: choice?.startsWith('min') ? Number(choice.slice(3)) : null,
          })
        }
        options={[
          { value: null, label: tp('smart.any') },
          { value: 'max180', label: tp('smart.duration.under3') },
          { value: 'max240', label: tp('smart.duration.under4') },
          { value: 'min300', label: tp('smart.duration.over5') },
        ]}
      />

      <Choice
        label={tp('smart.era')}
        value={rules.year_from ? `${rules.year_from}-${rules.year_to ?? ''}` : rules.year_to ? `-${rules.year_to}` : null}
        onChange={(choice) => {
          if (!choice) return set({ year_from: null, year_to: null });
          const [from, to] = choice.split('-');
          set({ year_from: from ? Number(from) : null, year_to: to ? Number(to) : null });
        }}
        options={[
          { value: null, label: tp('smart.any') },
          { value: '-1979', label: tp('smart.era.classic') },
          { value: '1980-1999', label: tp('smart.era.80s90s') },
          { value: '2000-2014', label: tp('smart.era.2000s') },
          { value: '2015-', label: tp('smart.era.recent') },
        ]}
      />

      <Choice
        label={tp('smart.listening')}
        value={
          rules.not_played_days ? `quiet${rules.not_played_days}` : rules.min_my_plays ? `plays${rules.min_my_plays}` : null
        }
        onChange={(choice) =>
          set({
            not_played_days: choice?.startsWith('quiet') ? Number(choice.slice(5)) : null,
            min_my_plays: choice?.startsWith('plays') ? Number(choice.slice(5)) : null,
          })
        }
        options={[
          { value: null, label: tp('smart.any') },
          { value: 'quiet30', label: tp('smart.listening.forgotten') },
          { value: 'quiet365', label: tp('smart.listening.never') },
          { value: 'plays3', label: tp('smart.listening.favourites') },
        ]}
      />

      <Choice
        label={tp('smart.sort')}
        value={rules.sort ?? 'recent'}
        onChange={(sort) => set({ sort: sort ?? 'recent' })}
        options={[
          { value: 'recent', label: tp('smart.sort.recent') },
          { value: 'popular', label: tp('smart.sort.popular') },
          { value: 'most_played', label: tp('smart.sort.most_played') },
          { value: 'least_played', label: tp('smart.sort.least_played') },
          { value: 'random', label: tp('smart.sort.random') },
        ]}
      />

      <Choice
        label={tp('smart.limit')}
        value={rules.limit ?? 50}
        onChange={(limit) => set({ limit: limit ?? 50 })}
        options={[25, 50, 100, 200].map((value) => ({ value, label: String(value) }))}
      />

      <div className="sticky bottom-0 mt-5 flex items-center gap-3 bg-[var(--sheet-bg,transparent)] pb-1 pt-2">
        <p className="min-w-0 flex-1 text-[12.5px] text-[var(--ink-dim)]" aria-live="polite">
          {preview.isFetching && !preview.data ? (
            <Spinner size={14} />
          ) : (
            tp('smart.matches', { count: preview.data?.total ?? 0 })
          )}
        </p>
        <button
          type="button"
          disabled={(!editing && !name.trim()) || pending}
          onClick={save}
          className="min-w-24 rounded-xl bg-[var(--accent)] px-4 py-2.5 text-[13.5px] font-bold text-[var(--accent-ink)] disabled:opacity-50"
        >
          {pending ? <Spinner size={16} /> : t('common.save')}
        </button>
      </div>
    </Sheet>
  );
}
