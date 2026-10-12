/**
 * Returns true if the event target is a text input element where
 * keyboard shortcuts should not fire (input, textarea, select, or
 * contentEditable). Use this guard in global `keydown` handlers to
 * avoid hijacking keystrokes meant for form fields.
 */
export function isTypingTarget(target: EventTarget | null): boolean {
	if (!(target instanceof HTMLElement)) return false;
	const tag = target.tagName;
	if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
	return target.isContentEditable;
}

/** A modal surface's root: the native `<dialog>` our `Dialog` renders (no
 * role), or an ARIA dialog (a Sheet, a popover). */
const DIALOG_SELECTOR = 'dialog[open], [role="dialog"], [role="alertdialog"]';

/** True when `target` sits inside an open dialog, which owns its own Escape:
 * a page-level Escape handler should leave the press to it. */
export function isInsideDialog(target: EventTarget | null): boolean {
	return target instanceof Element && target.closest(DIALOG_SELECTOR) != null;
}

/** True on Apple platforms, where the command key is ⌘ rather than Ctrl. */
export function isApplePlatform(): boolean {
	if (typeof navigator === 'undefined') return false;
	const nav = navigator as Navigator & { userAgentData?: { platform?: string } };
	const platform = nav.userAgentData?.platform ?? nav.platform ?? '';
	return /mac|iphone|ipad|ipod/i.test(platform);
}

/** The command modifier's label: `⌘` on Apple platforms, else `Ctrl`. */
function commandKeyLabel(): string {
	return isApplePlatform() ? '⌘' : 'Ctrl';
}

/** A command chord's visible label: `⌘K` on Apple platforms, else `Ctrl K`. */
export function commandChordLabel(key: string): string {
	const mod = commandKeyLabel();
	return `${mod}${mod === '⌘' ? '' : ' '}${key.toUpperCase()}`;
}

/** A command chord in `aria-keyshortcuts` syntax: `Meta+K` or `Control+K`. */
export function commandChordShortcut(key: string): string {
	return `${isApplePlatform() ? 'Meta' : 'Control'}+${key.toUpperCase()}`;
}

/** True when the platform's command modifier (⌘ / Ctrl) alone is held. */
export function hasCommandModifier(e: KeyboardEvent): boolean {
	if (e.altKey || e.shiftKey) return false;
	return isApplePlatform() ? e.metaKey && !e.ctrlKey : e.ctrlKey && !e.metaKey;
}
