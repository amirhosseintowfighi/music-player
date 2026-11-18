/** Runs the AutoMix analyser off the main thread: the music never waits for it. */
import { analyse } from './analysis';

interface Job {
  id: number;
  samples: Float32Array;
  sr: number;
}

self.onmessage = (event: MessageEvent<Job>) => {
  const { id, samples, sr } = event.data;
  try {
    self.postMessage({ id, result: analyse(samples, sr) });
  } catch (error) {
    self.postMessage({ id, error: String(error) });
  }
};
