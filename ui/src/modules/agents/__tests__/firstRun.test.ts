import { describe, it, expect, beforeEach } from 'vitest';
import type { AgentEntity } from '@/modules/agents/api';
import {
	deriveLanding,
	dismissFirstRun,
	firstRunDismissedKey,
	isFirstRunDismissed,
	resolveFirstRun,
	type BindingsRead,
} from '@/modules/agents/lib/firstRun';

function agent(id: string, status: AgentEntity['status'], createdAt = '2026-09-29T10:00:00Z') {
	return { id, name: id, status, createdAt } as AgentEntity;
}

const none = (): boolean => false;
const ready = (count: number): BindingsRead => ({ state: 'ready', count });

describe('resolveFirstRun', () => {
	it('no agents → listening', () => {
		expect(resolveFirstRun({ agents: [], bindings: ready(0), isDismissed: none })).toEqual({
			kind: 'listening',
		});
	});

	it('any agent at all, even only denied or archived ones → fleet', () => {
		for (const agents of [
			[agent('r', 'rejected')],
			[agent('x', 'archived')],
			[agent('r', 'rejected'), agent('x', 'archived')],
		]) {
			expect(resolveFirstRun({ agents, bindings: ready(0), isDismissed: none })).toEqual({
				kind: 'fleet',
			});
		}
	});

	it('only pending → arrival for the newest', () => {
		const agents = [
			agent('old', 'pending', '2026-09-29T09:00:00Z'),
			agent('new', 'pending', '2026-09-29T11:00:00Z'),
			agent('gone', 'rejected', '2026-09-29T12:00:00Z'),
		];
		expect(resolveFirstRun({ agents, bindings: ready(0), isDismissed: none })).toEqual({
			kind: 'arrival',
			agentId: 'new',
		});
	});

	it('active + pending → fleet', () => {
		const agents = [agent('a', 'active'), agent('p', 'pending')];
		expect(resolveFirstRun({ agents, bindings: ready(0), isDismissed: none })).toEqual({
			kind: 'fleet',
		});
	});

	it('lone active agent: waits for bindings, then approved only with none and not dismissed', () => {
		const agents = [agent('a', 'active'), agent('x', 'archived')];
		expect(resolveFirstRun({ agents, bindings: { state: 'loading' }, isDismissed: none })).toBe(
			null,
		);
		expect(resolveFirstRun({ agents, bindings: ready(0), isDismissed: none })).toEqual({
			kind: 'approved',
			agentId: 'a',
		});
		expect(resolveFirstRun({ agents, bindings: ready(1), isDismissed: none })).toEqual({
			kind: 'fleet',
		});
		expect(
			resolveFirstRun({ agents, bindings: { state: 'error' }, isDismissed: none }),
		).toEqual({ kind: 'fleet' });
		expect(
			resolveFirstRun({ agents, bindings: { state: 'loading' }, isDismissed: () => true }),
		).toEqual({ kind: 'fleet' });
	});

	it('a lone disabled agent, or two active ones → fleet', () => {
		for (const agents of [
			[agent('d', 'disabled')],
			[agent('a', 'active'), agent('b', 'active')],
		]) {
			expect(resolveFirstRun({ agents, bindings: ready(0), isDismissed: none })).toEqual({
				kind: 'fleet',
			});
		}
	});
});

describe('deriveLanding', () => {
	const derive = (
		agents: AgentEntity[],
		trackedId: string | null = null,
		deniedId: string | null = null,
	) => deriveLanding({ agents, trackedId, deniedId });

	it('nothing pending → listening; a working agent of its own hands off to the fleet', () => {
		expect(derive([agent('r', 'rejected')])).toEqual({
			agent: null,
			phase: 'listening',
			morePending: 0,
			handOff: false,
		});
		expect(derive([agent('d', 'disabled')])).toMatchObject({
			phase: 'listening',
			handOff: true,
		});
	});

	it('the newest pending arrives, and the rest are counted', () => {
		const agents = [
			agent('old', 'pending', '2026-09-29T09:00:00Z'),
			agent('new', 'pending', '2026-09-29T11:00:00Z'),
			agent('mid', 'pending', '2026-09-29T10:00:00Z'),
		];
		const view = derive(agents);
		expect(view.agent?.id).toBe('new');
		expect(view).toMatchObject({ phase: 'arrived', morePending: 2, handOff: false });
	});

	it("the tracked agent stays the card's once approved, and keeps others counted", () => {
		const agents = [
			agent('mine', 'active', '2026-09-29T09:00:00Z'),
			agent('other', 'pending', '2026-09-29T11:00:00Z'),
		];
		const view = derive(agents, 'mine');
		expect(view.agent?.id).toBe('mine');
		expect(view).toMatchObject({ phase: 'approved', morePending: 1, handOff: false });
	});

	it('a denied agent drops out at once, even while the roster still says pending', () => {
		const agents = [agent('mine', 'pending'), agent('next', 'pending', '2026-09-29T08:00:00Z')];
		expect(derive(agents, 'mine', 'mine')).toMatchObject({
			phase: 'arrived',
			morePending: 0,
			agent: expect.objectContaining({ id: 'next' }),
		});
		expect(derive([agent('mine', 'pending')], 'mine', 'mine').phase).toBe('listening');
		// Denied elsewhere: the roster says rejected.
		expect(derive([agent('mine', 'rejected')], 'mine').phase).toBe('listening');
	});
});

describe('first-run dismissal', () => {
	beforeEach(() => window.localStorage.clear());

	it('persists per agent under a namespaced key', () => {
		expect(firstRunDismissedKey('agnt_1')).toBe('j1.agents.firstRun.dismissed.agnt_1');
		expect(isFirstRunDismissed('agnt_1')).toBe(false);
		dismissFirstRun('agnt_1');
		expect(isFirstRunDismissed('agnt_1')).toBe(true);
		expect(isFirstRunDismissed('agnt_2')).toBe(false);
	});
});
