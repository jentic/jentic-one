import { describe, expect, it } from 'vitest';
import { recentFirst } from '@/modules/agents/lib/stripOrder';

const ids = (...names: string[]) => names.map((id) => ({ id }));
const order = (list: { id: string }[]) => list.map((a) => a.id);

describe('recentFirst', () => {
	it('puts the history first, newest first, and keeps the rest in place', () => {
		// Pin A, rail B C D E; select D, then C (the history leads with the pin).
		expect(order(recentFirst(ids('A', 'B', 'C', 'E'), ['D', 'A']))).toEqual([
			'A',
			'B',
			'C',
			'E',
		]);
		expect(order(recentFirst(ids('A', 'B', 'D', 'E'), ['C', 'D', 'A']))).toEqual([
			'D',
			'A',
			'B',
			'E',
		]);
	});

	it('skips ids no longer in the fleet, and leaves an empty history as is', () => {
		expect(order(recentFirst(ids('A', 'B', 'C'), ['gone', 'C']))).toEqual(['C', 'A', 'B']);
		expect(order(recentFirst(ids('A', 'B', 'C'), []))).toEqual(['A', 'B', 'C']);
	});

	it('reads a repeated id at its newest place', () => {
		expect(order(recentFirst(ids('A', 'B', 'C'), ['B', 'C', 'B']))).toEqual(['B', 'C', 'A']);
	});
});
