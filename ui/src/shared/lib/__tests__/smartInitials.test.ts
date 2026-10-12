import { describe, expect, it } from 'vitest';
import { initialsCandidates, smartInitials } from '@/shared/lib/smartInitials';

const fleet = (...names: string[]) => names.map((name, i) => ({ id: `a${i}`, name }));
const read = (...names: string[]) => [...smartInitials(fleet(...names)).values()];

describe('smartInitials', () => {
	it('lets a number carry the identity', () => {
		expect(read('my-agent-34', 'my-agent-34-staging', 'my-agent-1', 'research-bot-v2')).toEqual(
			['M34', 'M34S', 'M1', 'R2'],
		);
	});

	it('reads word initials for plain names', () => {
		expect(read('docs-indexer', 'lead-enricher', 'inbox')).toEqual(['DI', 'LE', 'IN']);
	});

	it('moves a clash group down the ranks until every member differs', () => {
		expect(read('support-triage', 'support-triage-eu')).toEqual(['ST', 'STE']);
		const three = read('sales-team', 'support-triage', 'support-triage-eu');
		expect(new Set(three).size).toBe(3);
	});

	it('leaves names that do not clash alone', () => {
		const [a, b] = read('support-triage', 'billing-reporter');
		expect([a, b]).toEqual(['ST', 'BR']);
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

	it('keeps every initial unique fleet-wide, across clash groups', () => {
		// Three identical names exhaust their candidates and number themselves;
		// the numbering must not land on `my-agent-343`'s own M343.
		const names = ['my-agent-34', 'my-agent-34', 'my-agent-34', 'my-agent-343'];
		const got = read(...names);
		expect(new Set(got).size).toBe(names.length);
		expect(got[3]).toBe('M343');
		expect(got.slice(0, 3)).not.toContain('M343');
	});

	it('is unique across a large near-identical fleet', () => {
		const names = Array.from({ length: 40 }, (_, i) =>
			i % 3 === 0 ? `my-agent-${30 + (i % 7)}` : `my-agent-34${i % 5}`,
		);
		const got = read(...names);
		expect(new Set(got).size).toBe(names.length);
		for (const v of got) expect(v.length).toBeLessThanOrEqual(4);
	});

	it('copes with a leading number', () => {
		expect(read('2fa-bot')).toEqual(['F2B']);
	});
});
