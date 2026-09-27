/**
 * One shared clock for the feed's relative times ("now", "4m"). Every row reads
 * the same ticking value, so a feed of hundreds of rows runs ONE interval, and
 * the interval only exists while something is subscribed.
 */
import { useSyncExternalStore } from 'react';

const TICK_MS = 15_000;

const listeners = new Set<() => void>();
let now = Date.now();
let timer: ReturnType<typeof setInterval> | undefined;

function subscribe(listener: () => void): () => void {
	listeners.add(listener);
	if (!timer) {
		now = Date.now();
		timer = setInterval(() => {
			now = Date.now();
			for (const l of listeners) l();
		}, TICK_MS);
	}
	return () => {
		listeners.delete(listener);
		if (listeners.size === 0 && timer) {
			clearInterval(timer);
			timer = undefined;
		}
	};
}

function getSnapshot(): number {
	return now;
}

export function useNow(): number {
	return useSyncExternalStore(subscribe, getSnapshot);
}
