/** The tour: what the bot is for first, back and forward, and commands that read right. */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { TourBody } from '@/components/Tour';
import { fa } from '@/i18n/fa';
import { en } from '@/i18n/en';
import { TOUR_KEY, TOUR_STEPS } from '@/lib/tour';

describe('tour', () => {
  it('starts with what the bot does and covers the bot chat', () => {
    const ids = TOUR_STEPS.map((s) => s.id);
    expect(ids.slice(0, 2)).toEqual(['welcome', 'channels']);
    expect(ids).toEqual(expect.arrayContaining(['song', 'player', 'bot', 'more', 'done']));
    expect(TOUR_KEY).toBe('tmusic.tour.v2');
  });

  it('has copy for every step in both languages', () => {
    for (const step of TOUR_STEPS) {
      for (const table of [fa, en] as Record<string, string>[]) {
        expect(table[step.titleKey], step.titleKey).toBeTruthy();
        expect(table[step.bodyKey], step.bodyKey).toBeTruthy();
      }
    }
    expect((fa as Record<string, string>)['tour.bot.body']).toContain('@{bot}');
  });

  it('shows commands and usernames as left-to-right chips', () => {
    render(
      <p>
        <TourBody text="• /jam یک جم بساز و @noax_bot را صدا کن" />
      </p>,
    );
    const chips = screen.getAllByText(/^(\/jam|@noax_bot)$/);
    expect(chips.map((c) => [c.tagName, c.getAttribute('dir')])).toEqual([
      ['CODE', 'ltr'],
      ['CODE', 'ltr'],
    ]);
  });
});
