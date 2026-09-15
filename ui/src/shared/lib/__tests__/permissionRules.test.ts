import { describe, expect, it } from 'vitest';
import { isUnrestrictedAllow, ruleSummary } from '../permissionRules';

describe('isUnrestrictedAllow', () => {
	it('is true only for an allow with no methods, path, or operations', () => {
		expect(
			isUnrestrictedAllow({ effect: 'allow', methods: null, path: null, operations: null }),
		).toBe(true);
		expect(
			isUnrestrictedAllow({ effect: 'allow', methods: [], path: null, operations: [] }),
		).toBe(true);
	});

	it('is false for a constrained allow or any non-allow effect', () => {
		expect(
			isUnrestrictedAllow({
				effect: 'allow',
				methods: ['GET'],
				path: null,
				operations: null,
			}),
		).toBe(false);
		expect(
			isUnrestrictedAllow({
				effect: 'allow',
				methods: null,
				path: '/v1/*',
				operations: null,
			}),
		).toBe(false);
		expect(
			isUnrestrictedAllow({ effect: 'deny', methods: null, path: null, operations: null }),
		).toBe(false);
		expect(
			isUnrestrictedAllow({
				effect: 'require-approval',
				methods: null,
				path: null,
				operations: null,
			}),
		).toBe(false);
	});
});

describe('ruleSummary', () => {
	it('describes an empty rule set as unrestricted', () => {
		expect(ruleSummary([])).toBe('No operation restrictions — full access to the resource.');
	});

	it('flags a condition-less allow as unrestricted', () => {
		expect(
			ruleSummary([{ effect: 'allow', methods: null, path: null, operations: null }]),
		).toBe('Allows ANY request (unrestricted).');
	});

	it('summarises allow/deny with methods, operations and path', () => {
		expect(
			ruleSummary([
				{
					effect: 'allow',
					methods: ['GET', 'POST'],
					operations: ['a', 'b', 'c'],
					path: null,
				},
			]),
		).toBe('Allows GET, POST on 3 operations.');
		expect(
			ruleSummary([{ effect: 'deny', methods: ['DELETE'], operations: null, path: null }]),
		).toBe('Blocks DELETE.');
		expect(
			ruleSummary([{ effect: 'allow', methods: null, operations: ['x'], path: '/v1/*' }]),
		).toBe('Allows 1 operation, scoped to path /v1/*.');
	});

	it('phrases the path clause per match mode', () => {
		expect(
			ruleSummary([
				{
					effect: 'deny',
					methods: null,
					operations: null,
					path: '/admin',
					match_mode: 'prefix',
				},
			]),
		).toBe('Blocks all requests, scoped to paths starting with /admin.');
		expect(
			ruleSummary([
				{
					effect: 'deny',
					methods: null,
					operations: null,
					path: '/admin',
					match_mode: 'exact',
				},
			]),
		).toBe('Blocks all requests, scoped to exactly path /admin.');
	});

	it('announces blocks before allows and falls back to "all requests"', () => {
		expect(
			ruleSummary([
				{ effect: 'allow', methods: ['GET'], operations: null, path: null },
				{ effect: 'deny', methods: null, operations: null, path: null },
			]),
		).toBe('Blocks all requests; Allows GET.');
	});
});
