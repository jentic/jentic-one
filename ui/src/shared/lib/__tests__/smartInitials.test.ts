import { describe, expect, it } from 'vitest';
import { initialsCandidates, smartInitials } from '@/shared/lib/smartInitials';

interface Agent {
	id: string;
	name: string;
	createdAt: string;
}

/** Created one minute apart, in argument order. */
const at = (i: number) => new Date(Date.UTC(2026, 0, 1, 0, i)).toISOString();
const fleet = (...names: string[]): Agent[] =>
	names.map((name, i) => ({ id: `a${i}`, name, createdAt: at(i) }));
const read = (...names: string[]) => [...smartInitials(fleet(...names)).values()];

/** Deterministic shuffle, so the order-independence check needs no randomness. */
function reversedPairs<T>(list: readonly T[]): T[] {
	const out = [...list].reverse();
	for (let i = 0; i + 1 < out.length; i += 2) [out[i], out[i + 1]] = [out[i + 1], out[i]];
	return out;
}

const FLEETS: string[][] = [
	['my-agent-34', 'my-agent-34-staging', 'my-agent-343'],
	['my-agent-34', 'my-agent-34', 'my-agent-34', 'my-agent-343'],
	['support-triage', 'support-triage-eu', 'sales-team'],
	['support-triage-eu', 'support-triage', 'support-triage'],
	['docs-indexer', 'lead-enricher', 'inbox', 'inbox', 'inbox-v2'],
	Array.from({ length: 30 }, (_, i) =>
		i % 3 === 0 ? `my-agent-${30 + (i % 7)}` : `my-agent-34${i % 5}`,
	),
];

describe('smartInitials', () => {
	it('lets a number carry the identity', () => {
		expect(read('my-agent-34', 'my-agent-34-staging', 'my-agent-1', 'research-bot-v2')).toEqual(
			['M34', 'M34S', 'M1', 'R2'],
		);
	});

	it('reads word initials for plain names', () => {
		expect(read('docs-indexer', 'lead-enricher', 'inbox')).toEqual(['DI', 'LE', 'IN']);
	});

	it('gives contested letters to the older agent; the newer one adapts', () => {
		expect(read('support-triage', 'support-triage-eu')).toEqual(['ST', 'STE']);
		// `support-triage-eu` first: it keeps ST, and the later name moves on.
		expect(read('support-triage-eu', 'support-triage')).toEqual(['ST', 'SUT']);
		const three = read('sales-team', 'support-triage', 'support-triage-eu');
		expect(new Set(three).size).toBe(3);
		expect(three[0]).toBe('ST');
	});

	it('never changes an existing agent when a newer one joins', () => {
		for (const names of FLEETS) {
			for (const newcomer of ['my-agent-34', 'my-agent-34-staging', 'support-triage', 'st']) {
				const before = fleet(...names);
				const after = [
					...before,
					{ id: 'new', name: newcomer, createdAt: at(names.length) },
				];
				const was = smartInitials(before);
				const now = smartInitials(after);
				for (const a of before) expect(now.get(a.id)).toBe(was.get(a.id));
				expect(now.get('new')).toBeDefined();
			}
		}
	});

	it('keeps a lone agent on its letters once a clashing name appears', () => {
		const lone = fleet('my-agent-34');
		expect(smartInitials(lone).get('a0')).toBe('M34');
		const grown = [...lone, { id: 'b', name: 'my-agent-34', createdAt: at(5) }];
		expect(smartInitials(grown).get('a0')).toBe('M34');
		expect(smartInitials(grown).get('b')).toBe('MA34');
	});

	it('depends on creation time and id only, not on the order the fleet arrives in', () => {
		for (const names of FLEETS) {
			const agents = fleet(...names);
			const expected = smartInitials(agents);
			const shuffled = smartInitials(reversedPairs(agents));
			for (const a of agents) expect(shuffled.get(a.id)).toBe(expected.get(a.id));
		}
	});

	it('returns the picks in the order the fleet was given', () => {
		const agents = fleet('docs-indexer', 'lead-enricher', 'inbox').reverse();
		expect([...smartInitials(agents).keys()]).toEqual(['a2', 'a1', 'a0']);
	});

	it('breaks a tie in creation time by id', () => {
		const same = '2026-01-01T00:00:00.000Z';
		const tie = (ids: [string, string]) =>
			smartInitials([
				{ id: ids[0], name: 'support-triage', createdAt: same },
				{ id: ids[1], name: 'support-triage-eu', createdAt: same },
			]);
		// The smaller id is served first, whichever name it carries.
		expect(tie(['b', 'a']).get('a')).toBe('ST');
		expect(tie(['a', 'b']).get('a')).toBe('ST');
		expect(tie(['b', 'a']).get('b')).toBe('SUT');
	});

	it('lets a younger agent improve once an older one is deleted', () => {
		const agents = fleet('support-triage', 'support-triage');
		expect(smartInitials(agents).get('a1')).toBe('SUT');
		// Initials are derived, not stored: with the older agent gone the
		// letters it held are free again.
		expect(smartInitials(agents.slice(1)).get('a1')).toBe('ST');
	});

	it('is deterministic and caps at four letters', () => {
		const names = ['alpha-beta-gamma-delta-epsilon', 'alpha-beta'];
		expect(read(...names)).toEqual(read(...names));
		for (const v of read(...names)) expect(v.length).toBeLessThanOrEqual(4);
	});

	it('skips an empty name rather than inventing letters', () => {
		expect(initialsCandidates('')).toEqual([]);
		expect(smartInitials(fleet('')).size).toBe(0);
	});

	it('keeps every initial unique fleet-wide, including the numbered fallback', () => {
		// Identical names walk their candidates (M34, MA34), then number the
		// first; the numbering never lands on letters another agent holds.
		const names = ['my-agent-34', 'my-agent-34', 'my-agent-34', 'my-agent-343'];
		expect(read(...names)).toEqual(['M34', 'MA34', 'M342', 'M343']);
		// Created first, `my-agent-343` keeps its own M343, and the numbering
		// steps over it.
		const first = read(
			'my-agent-343',
			'my-agent-34',
			'my-agent-34',
			'my-agent-34',
			'my-agent-34',
		);
		expect(first).toEqual(['M343', 'M34', 'MA34', 'M342', 'M344']);
	});

	it('is unique across every fleet, large and near-identical', () => {
		for (const names of FLEETS) {
			const got = read(...names);
			expect(got).toHaveLength(names.length);
			expect(new Set(got).size).toBe(names.length);
			for (const v of got) expect(v.length).toBeLessThanOrEqual(4);
		}
	});

	it('copes with a leading number', () => {
		expect(read('2fa-bot')).toEqual(['F2B']);
	});
});
