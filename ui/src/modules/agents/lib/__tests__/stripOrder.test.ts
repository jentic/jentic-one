import { describe, expect, it } from 'vitest';
import { railOrder, recentFirst } from '@/modules/agents/lib/stripOrder';
import type { ActorStatus } from '@/modules/agents/api';

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

describe('railOrder', () => {
	const fleet = (...items: [string, ActorStatus][]) =>
		items.map(([id, status]) => ({ id, status }));

	it('closes the rail with settled agents, in fleet order, whatever the history', () => {
		const agents = fleet(
			['A', 'active'],
			['B', 'rejected'],
			['C', 'active'],
			['D', 'archived'],
		);
		// B and D were opened lately; neither rides the history past the live agents.
		expect(order(railOrder(agents, ['B', 'D', 'C'], null))).toEqual(['C', 'A', 'B', 'D']);
	});

	it('keeps the agent just left beside the pin for that switch, even when settled', () => {
		const agents = fleet(['A', 'active'], ['B', 'rejected'], ['C', 'active']);
		expect(order(railOrder(agents, ['X', 'B', 'A'], 'B'))).toEqual(['B', 'A', 'C']);
		// One more switch, from A: B drops to the tail.
		expect(order(railOrder(agents, ['Y', 'A', 'X', 'B'], 'A'))).toEqual(['A', 'C', 'B']);
	});

	it('is recentFirst when nothing is settled', () => {
		const agents = fleet(['A', 'active'], ['B', 'pending'], ['C', 'disabled']);
		expect(order(railOrder(agents.slice(0, 2), ['B'], 'B'))).toEqual(
			order(recentFirst(agents.slice(0, 2), ['B'])),
		);
	});
});
