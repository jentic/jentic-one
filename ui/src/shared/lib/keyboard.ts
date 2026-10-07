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
