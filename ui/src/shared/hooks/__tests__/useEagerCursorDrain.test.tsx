import { describe, it, expect, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import {
	useEagerCursorDrain,
	type EagerCursorDrainSource,
} from '@/shared/hooks/useEagerCursorDrain';

function source(over: Partial<EagerCursorDrainSource> = {}): EagerCursorDrainSource {
	return {
		hasNextPage: true,
		isFetchingNextPage: false,
		isError: false,
		fetchNextPage: vi.fn(() => Promise.resolve()),
		...over,
	};
}

describe('useEagerCursorDrain', () => {
	it('fetches the next page whenever one exists and no fetch is in flight', () => {
		const s = source();
		renderHook((props: EagerCursorDrainSource) => useEagerCursorDrain(props), {
			initialProps: s,
		});
		expect(s.fetchNextPage).toHaveBeenCalledTimes(1);
	});

	it('does not fetch while a page fetch is already in flight, then resumes', () => {
		const fetchNextPage = vi.fn(() => Promise.resolve());
		const { rerender } = renderHook(
			(props: EagerCursorDrainSource) => useEagerCursorDrain(props),
			{ initialProps: source({ fetchNextPage, isFetchingNextPage: true }) },
		);
		expect(fetchNextPage).not.toHaveBeenCalled();

		// The in-flight page lands and another remains — the drain continues.
		rerender(source({ fetchNextPage, isFetchingNextPage: false }));
		expect(fetchNextPage).toHaveBeenCalledTimes(1);
	});

	it('stops once the roster is complete', () => {
		const s = source({ hasNextPage: false });
		renderHook((props: EagerCursorDrainSource) => useEagerCursorDrain(props), {
			initialProps: s,
		});
		expect(s.fetchNextPage).not.toHaveBeenCalled();
	});

	it('stops when the query errors, even though hasNextPage stays true (no request loop)', () => {
		const fetchNextPage = vi.fn(() => Promise.resolve());
		// TanStack v5 failure mode: after a failed fetchNextPage, hasNextPage
		// stays true and isFetchingNextPage returns false — only isError flips.
		const errored = source({ fetchNextPage, isError: true });
		const { rerender } = renderHook(
			(props: EagerCursorDrainSource) => useEagerCursorDrain(props),
			{ initialProps: errored },
		);
		expect(fetchNextPage).not.toHaveBeenCalled();

		// Unrelated rerenders while errored must not re-fire the fetch.
		rerender({ ...errored });
		rerender({ ...errored });
		expect(fetchNextPage).not.toHaveBeenCalled();

		// A successful retry clears the error state — the drain resumes.
		rerender(source({ fetchNextPage, isError: false }));
		expect(fetchNextPage).toHaveBeenCalledTimes(1);
	});
});
