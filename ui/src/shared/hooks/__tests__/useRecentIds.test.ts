/**
 * useRecentIds — most-recently-current ids, newest first, persisted per
 * storage key: the current id leads in the render it becomes current, the
 * list is capped and de-duplicated, and unreadable storage reads as empty.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { useRecentIds } from '@/shared/hooks/useRecentIds';

const KEY = 'test.recentIds';
const OTHER = 'test.otherRecentIds';

const stored = (key: string): unknown => JSON.parse(window.localStorage.getItem(key) ?? 'null');

interface Props {
	storageKey?: string;
	current: string | null;
	cap?: number;
}

function render(initial: Props) {
	const renders: (readonly string[])[] = [];
	const hook = renderHook(
		({ storageKey = KEY, current, cap }: Props) => {
			const ids = useRecentIds(storageKey, current, cap);
			renders.push(ids);
			return ids;
		},
		{ initialProps: initial },
	);
	return { ...hook, renders };
}

describe('useRecentIds', () => {
	beforeEach(() => window.localStorage.clear());
	afterEach(() => window.localStorage.clear());

	it('leads with the current id on the first render and persists the list', async () => {
		window.localStorage.setItem(KEY, JSON.stringify(['a', 'b']));
		const { result, renders } = render({ current: 'c' });
		expect(renders[0]).toEqual(['c', 'a', 'b']);
		expect(result.current).toEqual(['c', 'a', 'b']);
		await waitFor(() => expect(stored(KEY)).toEqual(['c', 'a', 'b']));
	});

	it('moves a newly current id to the front in the render it changes', () => {
		const { result, rerender, renders } = render({ current: 'a' });
		rerender({ current: 'b' });
		// No committed render shows the old order once `b` is current.
		expect(renders[renders.length - 1]).toEqual(['b', 'a']);
		expect(result.current).toEqual(['b', 'a']);
	});

	it('does not duplicate a repeat selection and keeps the order', () => {
		const { result, rerender } = render({ current: 'a' });
		rerender({ current: 'b' });
		rerender({ current: 'c' });
		rerender({ current: 'c' });
		expect(result.current).toEqual(['c', 'b', 'a']);
		rerender({ current: 'a' });
		expect(result.current).toEqual(['a', 'c', 'b']);
	});

	it('keeps the history unchanged while nothing is current', () => {
		window.localStorage.setItem(KEY, JSON.stringify(['a', 'b']));
		const { result, rerender } = render({ current: null });
		expect(result.current).toEqual(['a', 'b']);
		rerender({ current: 'b' });
		rerender({ current: null });
		expect(result.current).toEqual(['b', 'a']);
	});

	it('caps the list, on read and when the cap changes', async () => {
		window.localStorage.setItem(KEY, JSON.stringify(['a', 'b', 'c', 'd']));
		const { result, rerender } = render({ current: 'e', cap: 3 });
		expect(result.current).toEqual(['e', 'a', 'b']);
		rerender({ current: 'e', cap: 2 });
		expect(result.current).toEqual(['e', 'a']);
		await waitFor(() => expect(stored(KEY)).toEqual(['e', 'a']));
	});

	it('defaults to a cap of 50', () => {
		const many = Array.from({ length: 60 }, (_, i) => `id${i}`);
		window.localStorage.setItem(KEY, JSON.stringify(many));
		const { result } = render({ current: 'new' });
		expect(result.current).toHaveLength(50);
		expect(result.current[0]).toBe('new');
	});

	it.each([
		['malformed JSON', '{not json'],
		['a non-array', JSON.stringify({ a: 1 })],
		['a string', JSON.stringify('a')],
	])('reads %s as an empty history', (_label, raw) => {
		window.localStorage.setItem(KEY, raw);
		expect(render({ current: null }).result.current).toEqual([]);
	});

	it('drops entries that are not non-empty strings', () => {
		window.localStorage.setItem(KEY, JSON.stringify(['a', 1, '', null, 'b']));
		expect(render({ current: null }).result.current).toEqual(['a', 'b']);
	});

	it('re-reads a new storage key and does not write the old history under it', async () => {
		window.localStorage.setItem(KEY, JSON.stringify(['a', 'b']));
		window.localStorage.setItem(OTHER, JSON.stringify(['x', 'y']));
		const { result, rerender } = render({ current: 'a' });
		rerender({ storageKey: OTHER, current: 'y' });
		expect(result.current).toEqual(['y', 'x']);
		await waitFor(() => expect(stored(OTHER)).toEqual(['y', 'x']));
		expect(stored(KEY)).toEqual(['a', 'b']);
	});
});
