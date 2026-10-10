/**
 * usePinStack — the rows a list holds open on purpose, any number at once,
 * remembered in the order they were pinned.
 *
 *  - `toggle(key)` pins a row, or unpins just that row if it is pinned; the
 *    other pins are never touched.
 *  - Escape unpins the most recently pinned row (a stack), one per press —
 *    unless the press is already claimed (`defaultPrevented`, e.g. a hover
 *    preview folding first) or lands in a dialog.
 *  - `clear()` lets every pin go; `unpin(key)` just one.
 *  - `pinAll(keys)` pins every row, in list order, a beat apart
 *    (`PIN_ALL_STAGGER`: ~28ms each, the whole run capped at ~0.5s), so a
 *    long list doesn't mount every reveal in one frame (`clear()` folds them
 *    together: a fold mounts nothing). Reduced motion pins them all at once.
 *    Any other change (a toggle, an unpin, Escape, clear) stops a run still
 *    going.
 *
 * The keys are the caller's; a key the caller no longer draws is the caller's
 * to `unpin`. Pair it with `useHoverIntent({ pinned })` so hover leaves the
 * pinned rows alone.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useReducedMotionConfig } from 'framer-motion';
import { isInsideDialog } from '@/shared/lib/keyboard';

/** How `pinAll` spaces the rows it pins. */
const PIN_ALL_STAGGER = {
	/** Between one row and the next. */
	stepMs: 28,
	/** The longest a whole run takes, however long the list. */
	maxRunMs: 500,
} as const;

export interface PinStack {
	/** Pinned keys, oldest first. */
	keys: readonly string[];
	isPinned: (key: string) => boolean;
	toggle: (key: string) => void;
	unpin: (key: string) => void;
	clear: () => void;
	/** Pin every one of `keys` (kept in that order), a beat apart. */
	pinAll: (keys: readonly string[]) => void;
}

export function usePinStack(): PinStack {
	const reduced = useReducedMotionConfig() ?? false;
	const [keys, setKeys] = useState<readonly string[]>([]);
	/** A `pinAll` run still going. */
	const run = useRef<ReturnType<typeof setTimeout>[]>([]);
	const stopRun = useCallback(() => {
		for (const timer of run.current) clearTimeout(timer);
		run.current = [];
	}, []);
	useEffect(() => stopRun, [stopRun]);

	const toggle = useCallback(
		(key: string) => {
			stopRun();
			setKeys((prev) =>
				prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key],
			);
		},
		[stopRun],
	);
	const unpin = useCallback(
		(key: string) => {
			stopRun();
			setKeys((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : prev));
		},
		[stopRun],
	);
	const clear = useCallback(() => {
		stopRun();
		setKeys((prev) => (prev.length === 0 ? prev : []));
	}, [stopRun]);
	const pinAll = useCallback(
		(all: readonly string[]) => {
			stopRun();
			const add = (key: string) =>
				setKeys((prev) => (prev.includes(key) ? prev : [...prev, key]));
			if (reduced || all.length <= 1) {
				setKeys((prev) => [...prev, ...all.filter((k) => !prev.includes(k))]);
				return;
			}
			const step = Math.min(
				PIN_ALL_STAGGER.stepMs,
				PIN_ALL_STAGGER.maxRunMs / (all.length - 1),
			);
			// The first at once, so whatever reads the stack says so this frame.
			add(all[0]!);
			run.current = all.slice(1).map((key, i) => setTimeout(() => add(key), step * (i + 1)));
		},
		[reduced, stopRun],
	);
	// On `window`, so a document-level handler (a hover preview's Escape) has
	// already had its say and can claim the press.
	const anyPinned = keys.length > 0;
	useEffect(() => {
		if (!anyPinned) return;
		const onKey = (event: KeyboardEvent) => {
			if (event.key !== 'Escape' || event.defaultPrevented) return;
			if (isInsideDialog(event.target)) return;
			stopRun();
			setKeys((prev) => prev.slice(0, -1));
		};
		window.addEventListener('keydown', onKey);
		return () => window.removeEventListener('keydown', onKey);
	}, [anyPinned, stopRun]);

	const isPinned = useCallback((key: string) => keys.includes(key), [keys]);
	return useMemo(
		() => ({ keys, isPinned, toggle, unpin, clear, pinAll }),
		[keys, isPinned, toggle, unpin, clear, pinAll],
	);
}
