import { describe, it, expect, afterEach, vi } from 'vitest';
import { act, renderHook } from '@/__tests__/test-utils';
import { useJustLanded } from '@/modules/discover/lib/useJustLanded';

const set = (...ids: string[]): ReadonlySet<string> => new Set(ids);

describe('useJustLanded', () => {
	afterEach(() => {
		vi.useRealTimers();
	});

	it('flags ids that leave the pending set, then clears them after the timer', () => {
		vi.useFakeTimers();
		const { result, rerender } = renderHook(({ pending }) => useJustLanded(pending, 1000), {
			initialProps: { pending: set('a') },
		});
		expect([...result.current]).toEqual([]);

		rerender({ pending: set() });
		expect([...result.current]).toEqual(['a']);

		act(() => vi.advanceTimersByTime(1000));
		expect([...result.current]).toEqual([]);
	});

	it('overlapping batches each clear on their own timer', () => {
		vi.useFakeTimers();
		const { result, rerender } = renderHook(({ pending }) => useJustLanded(pending, 1000), {
			initialProps: { pending: set('a', 'b', 'c') },
		});

		// `a` lands at t=0.
		rerender({ pending: set('b', 'c') });
		act(() => vi.advanceTimersByTime(400));
		// A pending-set change mid-flash (`b` lands at t=400) must not cancel
		// `a`'s removal — that used to leave `a` flagged for good.
		rerender({ pending: set('c') });
		expect([...result.current].sort()).toEqual(['a', 'b']);
		// …nor does an unrelated change (a new import starting).
		act(() => vi.advanceTimersByTime(300));
		rerender({ pending: set('c', 'd') });

		// t=1000: only `a`'s batch has run out.
		act(() => vi.advanceTimersByTime(300));
		expect([...result.current]).toEqual(['b']);

		// t=1400: `b`'s too.
		act(() => vi.advanceTimersByTime(400));
		expect([...result.current]).toEqual([]);
	});

	it('clears every outstanding timer on unmount', () => {
		vi.useFakeTimers();
		const { rerender, unmount } = renderHook(({ pending }) => useJustLanded(pending, 1000), {
			initialProps: { pending: set('a', 'b') },
		});
		rerender({ pending: set('b') });
		rerender({ pending: set() });
		expect(vi.getTimerCount()).toBe(2);

		unmount();
		expect(vi.getTimerCount()).toBe(0);
	});
});
