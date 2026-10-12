/**
 * useCommandHotkey — a ⌘+key (Ctrl+key off Apple platforms) shortcut, bound at
 * the window.
 *
 * Unlike {@link useHotkey}'s bare letters, a command chord is deliberate, so it
 * fires from inside a text field too (⌘K in a filter is the convention). It
 * still stands aside on an auto-repeat and while another overlay owns the
 * screen — unless `whileOverlay` names that overlay as the hotkey's own (so the
 * same chord can close what it opened). `enabled: false` unbinds entirely.
 */
import { useEffect, useRef } from 'react';
import { hasCommandModifier } from '@/shared/lib/keyboard';

function overlayIsOpen(): boolean {
	return (
		document.querySelector('dialog[open], [role="dialog"][aria-modal="true"]:not([hidden])') !=
		null
	);
}

export function useCommandHotkey(
	key: string,
	handler: () => void,
	{ enabled = true, whileOverlay = false }: { enabled?: boolean; whileOverlay?: boolean } = {},
): void {
	const handlerRef = useRef(handler);
	handlerRef.current = handler;

	useEffect(() => {
		if (!enabled) return;
		function onKeyDown(e: KeyboardEvent) {
			if (e.key.toLowerCase() !== key.toLowerCase() || !hasCommandModifier(e)) return;
			if (e.repeat || (!whileOverlay && overlayIsOpen())) return;
			e.preventDefault();
			handlerRef.current();
		}
		window.addEventListener('keydown', onKeyDown);
		return () => window.removeEventListener('keydown', onKeyDown);
	}, [key, enabled, whileOverlay]);
}
