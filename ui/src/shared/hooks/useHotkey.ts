/**
 * useHotkey — a single-character page shortcut, bound at the window.
 *
 * Deliberately narrow, because a bare-letter shortcut is easy to fire by
 * accident. It never runs when:
 *   - any modifier is held (Cmd/Ctrl/Alt own their own shortcuts — `Cmd+/`
 *     stays PageHelp's);
 *   - the key is auto-repeating (holding a key must not fire N times);
 *   - the event target is a text-entry surface (typing "n" in a filter box is
 *     typing, not a command);
 *   - an overlay owns the screen. A modal dialog or sheet is a focused task —
 *     its Escape is the only shortcut that should reach through it, and a
 *     hotkey that opened a second overlay behind the first would strand the
 *     operator.
 *
 * `enabled: false` unbinds entirely, so a caller can gate a shortcut on the
 * action actually being available rather than firing a no-op.
 */
import { useEffect, useRef } from 'react';
import { isTypingTarget } from '@/shared/lib/keyboard';

/**
 * True while any overlay is open — a native modal `<dialog>` (the shared
 * `Dialog`) or a `SheetPrimitive`, which marks itself `role="dialog"` +
 * `aria-modal`.
 */
function overlayIsOpen(): boolean {
	return document.querySelector('dialog[open], [role="dialog"][aria-modal="true"]') != null;
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
