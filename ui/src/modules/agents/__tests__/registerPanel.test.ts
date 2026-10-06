import { describe, it, expect } from 'vitest';
import type { AgentEntity } from '@/modules/agents/api';
import { ARRIVAL_CLOCK_SKEW_MS, derivePanelArrival } from '@/modules/agents/lib/registerPanel';

const OPENED = Date.parse('2026-09-30T12:00:00Z');
const at = (offsetMs: number) => new Date(OPENED + offsetMs).toISOString();

function agent(id: string, status: AgentEntity['status'], createdAt: string, name = id) {
	return { id, name, status, createdAt } as AgentEntity;
}

function derive(
	agents: AgentEntity[],
	over: Partial<Parameters<typeof derivePanelArrival>[0]> = {},
) {
	return derivePanelArrival({
		agents,
		openedAt: OPENED,
		knownAtOpen: new Set(),
		trackedId: null,
		deniedId: null,
		expectedName: 'my-first-agent',
		...over,
	});
}

describe('derivePanelArrival', () => {
	it('listens while nothing registered after the opening', () => {
		const view = derive([
			agent('old', 'pending', at(-60_000)),
			agent('a', 'active', at(1_000)),
		]);
		expect(view).toEqual({ agent: null, phase: 'listening', morePending: 0 });
	});

	it('an agent the roster held at the opening is no arrival, even inside the skew window', () => {
		const known = agent('known', 'pending', at(-1_000));
		expect(derive([known], { knownAtOpen: new Set(['known']) }).agent).toBeNull();
		// Unseen and a little before the browser's clock: the server's clock may lag.
		expect(derive([known]).agent?.id).toBe('known');
		expect(
			derive([agent('early', 'pending', at(-ARRIVAL_CLOCK_SKEW_MS - 1))]).agent,
		).toBeNull();
	});

	it('prefers the arrival carrying the expected name, then the newest; counts the rest', () => {
		const agents = [
			agent('mine', 'pending', at(1_000), 'my-first-agent'),
			agent('newer', 'pending', at(2_000), 'other'),
			agent('older', 'pending', at(-60_000), 'my-first-agent'),
		];
		expect(derive(agents)).toMatchObject({
			agent: { id: 'mine' },
			phase: 'arrived',
			morePending: 1,
		});
		expect(derive(agents, { expectedName: 'nobody' }).agent?.id).toBe('newer');
	});

	it('keeps the tracked agent through approval, and drops a denied one', () => {
		const approved = agent('t', 'active', at(1_000));
		expect(derive([approved], { trackedId: 't' })).toMatchObject({
			agent: { id: 't' },
			phase: 'approved',
		});
		const pending = agent('t', 'pending', at(1_000));
		expect(derive([pending], { trackedId: 't', deniedId: 't' }).phase).toBe('listening');
		expect(derive([agent('t', 'rejected', at(1_000))], { trackedId: 't' }).agent).toBeNull();
	});
});
