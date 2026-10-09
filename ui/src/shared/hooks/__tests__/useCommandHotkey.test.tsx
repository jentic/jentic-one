import { describe, it, expect, vi } from 'vitest';
import { renderHook, render, fireEvent } from '@/__tests__/test-utils';
import { useCommandHotkey } from '@/shared/hooks/useCommandHotkey';
import { isApplePlatform } from '@/shared/lib/keyboard';

/** The platform's command chord on `key`, as the window sees it. */
function chord(key: string, init: KeyboardEventInit = {}) {
	const mod = isApplePlatform() ? { metaKey: true } : { ctrlKey: true };
	fireEvent.keyDown(window, { key, ...mod, ...init });
}

describe('useCommandHotkey', () => {
	it('fires on the command chord, case-insensitively', () => {
		const handler = vi.fn();
		renderHook(() => useCommandHotkey('k', handler));
		chord('k');
		chord('K');
		expect(handler).toHaveBeenCalledTimes(2);
	});

	it('ignores the bare key, extra modifiers and auto-repeat', () => {
		const handler = vi.fn();
		renderHook(() => useCommandHotkey('k', handler));
		fireEvent.keyDown(window, { key: 'k' });
		chord('k', { shiftKey: true });
		chord('k', { altKey: true });
		chord('k', { repeat: true });
		expect(handler).not.toHaveBeenCalled();
	});

	it('fires from inside a text field', () => {
		const handler = vi.fn();
		renderHook(() => useCommandHotkey('k', handler));
		const { getByRole } = render(<input aria-label="Filter" />);
		const input = getByRole('textbox');
		input.focus();
		const mod = isApplePlatform() ? { metaKey: true } : { ctrlKey: true };
		fireEvent.keyDown(input, { key: 'k', ...mod });
		expect(handler).toHaveBeenCalledOnce();
	});

	it('stands aside while another overlay is open, unless it is its own', () => {
		const handler = vi.fn();
		render(<dialog open>other</dialog>);
		const { rerender } = renderHook(
			({ whileOverlay }) => useCommandHotkey('k', handler, { whileOverlay }),
			{ initialProps: { whileOverlay: false } },
		);
		chord('k');
		expect(handler).not.toHaveBeenCalled();
		rerender({ whileOverlay: true });
		chord('k');
		expect(handler).toHaveBeenCalledOnce();
	});

	it('unbinds when disabled, and calls the latest handler', () => {
		const first = vi.fn();
		const second = vi.fn();
		const { rerender } = renderHook(
			({ handler, enabled }) => useCommandHotkey('k', handler, { enabled }),
			{ initialProps: { handler: first, enabled: false } },
		);
		chord('k');
		expect(first).not.toHaveBeenCalled();
		rerender({ handler: second, enabled: true });
		chord('k');
		expect(first).not.toHaveBeenCalled();
		expect(second).toHaveBeenCalledOnce();
	});
});
