/**
 * DJ: a set built for this listener, introduced as it goes.
 *
 * The server hands back segments — favourites, something from a while ago, new to
 * them, new releases — and this store plays them as one queue. Each time a segment
 * starts, the DJ says what is coming: out loud where the device has a voice for the
 * language (the Web Speech API, nothing to download), and always as a line on screen.
 * The music ducks under the voice and comes back up after.
 */
import { create } from 'zustand';

import type { Track } from '@/api/client';
import { fetchDj, type DjSegment } from '@/api/listening';
import { translate, type Key } from '@/i18n';
import { artistNames } from '@/lib/format';
import { fadeTo } from '@/player/engine';
import { usePlayer } from '@/store/player';
import { useUi } from '@/store/ui';

const INTRO: Record<DjSegment['kind'], Key> = {
  favorites: 'dj.intro.favorites',
  throwback: 'dj.intro.throwback',
  discovery: 'dj.intro.discovery',
  new: 'dj.intro.new',
};

interface DjStore {
  on: boolean;
  loading: boolean;
  /** Where each segment starts in the queue, and what it is. */
  marks: { at: number; kind: DjSegment['kind']; first: Track }[];
  /** The line the DJ is saying now (shown in the player). */
  line: string | null;
  start: () => Promise<boolean>;
  stop: () => void;
}

let spokenAt = -1;
let unsubscribe: (() => void) | null = null;

function voiceFor(lang: string): SpeechSynthesisVoice | null {
  const synth = globalThis.speechSynthesis;
  if (!synth) return null;
  const wanted = lang === 'fa' ? ['fa', 'fa-IR'] : ['en', 'en-US', 'en-GB'];
  return synth.getVoices().find((voice) => wanted.some((code) => voice.lang.startsWith(code))) ?? null;
}

async function say(text: string): Promise<void> {
  const lang = useUi.getState().lang;
  const voice = voiceFor(lang);
  if (!voice) return;
  const volume = usePlayer.getState().volume;
  await fadeTo(volume * 0.25, 400);
  await new Promise<void>((resolve) => {
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.voice = voice;
    utterance.lang = voice.lang;
    utterance.rate = 1.02;
    utterance.onend = () => resolve();
    utterance.onerror = () => resolve();
    // Never hold the music down for long, whatever the speech engine does.
    setTimeout(resolve, 9000);
    globalThis.speechSynthesis.speak(utterance);
  });
  await fadeTo(volume, 600);
}

export const useDj = create<DjStore>((set, get) => ({
  on: false,
  loading: false,
  marks: [],
  line: null,

  async start() {
    set({ loading: true });
    try {
      const dj = await fetchDj();
      const queue: Track[] = [];
      const marks: DjStore['marks'] = [];
      for (const segment of dj.segments) {
        const first = segment.items[0];
        if (!first) continue;
        marks.push({ at: queue.length, kind: segment.kind, first });
        queue.push(...segment.items);
      }
      if (queue.length === 0) return false;
      spokenAt = -1;
      set({ on: true, marks, line: null });
      unsubscribe?.();
      unsubscribe = usePlayer.subscribe((state, previous) => {
        if (!get().on) return;
        // The listener started something else: the DJ steps aside.
        if (state.queue !== previous.queue && state.queue[0]?.id !== queue[0]?.id) {
          get().stop();
          return;
        }
        if (state.index !== previous.index || state.current?.id !== previous.current?.id) announce(state.index);
      });
      await usePlayer.getState().play({ queue, index: 0, source: 'discover' });
      announce(0);
      return true;
    } catch {
      return false;
    } finally {
      set({ loading: false });
    }
  },

  stop() {
    unsubscribe?.();
    unsubscribe = null;
    globalThis.speechSynthesis?.cancel();
    set({ on: false, marks: [], line: null });
  },
}));

function announce(index: number): void {
  const { marks, on } = useDj.getState();
  if (!on || index === spokenAt) return;
  const mark = marks.find((item) => item.at === index);
  if (!mark) return;
  spokenAt = index;
  const lang = useUi.getState().lang;
  const line = translate(lang, INTRO[mark.kind], {
    artist: artistNames(mark.first) || mark.first.title,
    title: mark.first.title,
  });
  useDj.setState({ line });
  useUi.getState().toast(`🎙 ${line}`);
  void say(line).finally(() => {
    if (useDj.getState().line === line) useDj.setState({ line: null });
  });
}
