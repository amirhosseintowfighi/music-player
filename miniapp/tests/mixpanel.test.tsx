/** The AutoMix panel in the player: what is being mixed, and how. */
import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { MixPanel } from '@/components/MixPanel';
import { useAudioSettings } from '@/store/audio';
import { usePlayer } from '@/store/player';

import { makeTrack, renderApp } from './helpers';

describe('mix panel', () => {
  it('shows the transition while it plays, and the deck readout between', () => {
    useAudioSettings.setState({ automix: true });
    const track = makeTrack({
      id: 3,
      mix: { v: 1, bpm: 124, beat: 0.2, downbeat: 0.2, key: 7, intro: 15, outro: 180, confidence: 0.7 },
    });
    usePlayer.setState({ mixing: null });
    const { unmount } = renderApp(<MixPanel track={track} />);
    expect(screen.getByText(/124 BPM · 8A/)).toBeTruthy();
    unmount();

    usePlayer.setState({
      mixing: { style: 'blend', fromBpm: 124, toBpm: 126, fromKey: 7, toKey: 8, started: Date.now(), until: Date.now() + 15_000 },
    });
    renderApp(<MixPanel track={track} />);
    expect(screen.getByRole('status').textContent).toContain('میکس هم‌ضرب');
    expect(screen.getByRole('status').textContent).toContain('124→126 BPM · 8A→9A');
    usePlayer.setState({ mixing: null });
    useAudioSettings.setState({ automix: false });
  });
});
