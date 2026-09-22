/**
 * useHotkey — a single-character page shortcut, bound at the window.
 *
 * A bare letter is easy to fire by accident, so it never runs under a modifier, on
 * an auto-repeat, with focus in a text-entry surface, or while an overlay owns the
 * screen. `enabled: false` unbinds entirely.
 */
import { useEffect, useRef } from 'react';
import { isTypingTarget } from '@/shared/lib/keyboard';

/** True while any overlay is open. A closed `keepMounted` sheet stays in the DOM
 * marked `hidden`, so it must not count. */
function overlayIsOpen(): boolean {
	return (
		document.querySelector('dialog[open], [role="dialog"][aria-modal="true"]:not([hidden])') !=
		null
	);
}

export function useHotkey(key: string, handler: () => void, enabled = true): void {
	// The handler is read through a ref so a caller can pass an inline closure
	// without re-binding the listener on every render.
	const handlerRef = useRef(handler);
	handlerRef.current = handler;

	useEffect(() => {
		if (!enabled) return;
		function onKeyDown(e: KeyboardEvent) {
			if (e.key !== key) return;
			if (e.metaKey || e.ctrlKey || e.altKey || e.repeat) return;
			if (isTypingTarget(e.target) || overlayIsOpen()) return;
			e.preventDefault();
			handlerRef.current();
		}
		window.addEventListener('keydown', onKeyDown);
		return () => window.removeEventListener('keydown', onKeyDown);
	}, [key, enabled]);
}
