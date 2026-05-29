/**
 * Session-only "recently inspected" store for the Discover sheet (P3).
 *
 * Keeps a ring buffer of the last `RING_CAP` distinct APIs the user
 * opened in the sheet, deduped by `api_id` (the most-recent open wins
 * its slot — no duplicates in the strip). Persisted to `sessionStorage`
 * so a refresh keeps it but a new tab doesn't inherit; the sheet is a
 * navigation aid, not a profile artefact.
 *
 * Public API:
 *   - `useRecentInspects()` — sorted-by-recency entries (most recent
 *     first) + a `pushRecent(entry)` mutator.
 *   - `pushRecent(entry)` — direct mutator for callers that don't have
 *     hook access (e.g. router useEffect).
 *
 * Storage layout (`sessionStorage["discover.recentInspect.v1"]`):
 *   `RecentInspectEntry[]` — JSON-encoded. Schema is versioned in the
 *   key so a future shape change can ignore the stale array instead of
 *   crashing on parse.
 */

import { useCallback, useEffect, useState } from 'react';

const STORAGE_KEY = 'discover.recentInspect.v1';
const RING_CAP = 5;

export interface RecentInspectEntry {
	apiId: string;
	name?: string;
	source?: 'workspace' | 'directory' | string;
	inspectedAt: number;
}

type Listener = () => void;
const listeners = new Set<Listener>();

function readStorage(): RecentInspectEntry[] {
	if (typeof window === 'undefined') return [];
	try {
		const raw = window.sessionStorage.getItem(STORAGE_KEY);
		if (!raw) return [];
		const parsed = JSON.parse(raw);
		if (!Array.isArray(parsed)) return [];
		return parsed.filter(
			(e): e is RecentInspectEntry =>
				e &&
				typeof e === 'object' &&
				typeof e.apiId === 'string' &&
				typeof e.inspectedAt === 'number',
		);
	} catch {
		return [];
	}
}

function writeStorage(entries: RecentInspectEntry[]): void {
	if (typeof window === 'undefined') return;
	try {
		window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
	} catch {
		// quota / privacy mode — silently drop, the strip just won't
		// remember across reloads.
	}
}

function notify(): void {
	for (const fn of listeners) fn();
}

/**
 * Push an entry to the front of the recents ring. Dedupes by `apiId`
 * (existing entry is removed before the new one is prepended), caps at
 * `RING_CAP`, and notifies subscribers.
 */
export function pushRecent(entry: Omit<RecentInspectEntry, 'inspectedAt'>): void {
	const now = Date.now();
	const next: RecentInspectEntry = { ...entry, inspectedAt: now };
	const existing = readStorage().filter((e) => e.apiId !== entry.apiId);
	const merged = [next, ...existing].slice(0, RING_CAP);
	writeStorage(merged);
	notify();
}

/**
 * Clear the recents ring entirely. Exposed for tests; not wired to UI.
 */
export function clearRecents(): void {
	writeStorage([]);
	notify();
}

/**
 * React hook returning the recents (most-recent first) and a stable
 * `push` callback. Re-renders the consumer when any caller (this tab
 * only) mutates the ring.
 */
export function useRecentInspects(): {
	entries: RecentInspectEntry[];
	push: (entry: Omit<RecentInspectEntry, 'inspectedAt'>) => void;
} {
	const [entries, setEntries] = useState<RecentInspectEntry[]>(() => readStorage());

	useEffect(() => {
		const sync = () => setEntries(readStorage());
		listeners.add(sync);
		return () => {
			listeners.delete(sync);
		};
	}, []);

	const push = useCallback((entry: Omit<RecentInspectEntry, 'inspectedAt'>) => {
		pushRecent(entry);
	}, []);

	return { entries, push };
}
