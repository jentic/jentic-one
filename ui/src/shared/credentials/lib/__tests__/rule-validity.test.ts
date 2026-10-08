import { describe, expect, it } from 'vitest';
import { hasTemplatePlaceholder, ruleValidityIssue } from '@/shared/credentials/lib/rule-matcher';
import type { PermissionRule } from '@/shared/credentials/api/vendors-types';

/**
 * ``ruleValidityIssue`` is the single verdict every rules surface reads to
 * answer "can this rule ever match?" — the row's "never matches" badge, the
 * ``(regex)`` qualifier, and the draft form's inline warning all derive from
 * it. These cases pin the dead-rule shapes AND, just as importantly, the
 * deliberate patterns that must stay unflagged.
 */

const rule = (over: Partial<PermissionRule>): PermissionRule => ({
	effect: 'allow',
	methods: ['GET'],
	...over,
});

describe('ruleValidityIssue — regex paths that can never match', () => {
	it('flags an op-template path left in regex mode', () => {
		expect(
			ruleValidityIssue(rule({ path: '/repos/{owner}/{repo}', match_mode: 'regex' })),
		).toBe('placeholder-in-regex');
	});

	it('flags a template path with no mode — the backend reads it as regex', () => {
		expect(ruleValidityIssue(rule({ path: '/users/{username}' }))).toBe('placeholder-in-regex');
	});

	it('reports the more fundamental verdict when the pattern is also malformed', () => {
		expect(ruleValidityIssue(rule({ path: '/repos/{owner}/(', match_mode: 'regex' }))).toBe(
			'invalid-regex',
		);
	});

	it('still flags empty and unparseable patterns', () => {
		expect(ruleValidityIssue(rule({ path: '', match_mode: 'regex' }))).toBe('empty-regex');
		expect(ruleValidityIssue(rule({ path: '/repos/(', match_mode: 'regex' }))).toBe(
			'invalid-regex',
		);
	});
});

describe('ruleValidityIssue — deliberate patterns stay unflagged', () => {
	it('leaves the documented catch-all alone', () => {
		expect(ruleValidityIssue(rule({ path: '.*', match_mode: 'regex' }))).toBeNull();
	});

	it('leaves a hand-written single-segment wildcard alone', () => {
		expect(
			ruleValidityIssue(rule({ path: '/repos/[^/]+/[^/]+', match_mode: 'regex' })),
		).toBeNull();
	});

	it('reads {n}, {n,} and {n,m} as repetition quantifiers, not placeholders', () => {
		for (const path of ['/v[0-9]{1}/charges', '/x{2,}/y', '/x{2,5}/y']) {
			expect(ruleValidityIssue(rule({ path, match_mode: 'regex' }))).toBeNull();
		}
	});

	it('leaves a template path alone under prefix and exact — {…} is a wildcard there', () => {
		expect(
			ruleValidityIssue(rule({ path: '/repos/{owner}/{repo}', match_mode: 'exact' })),
		).toBeNull();
		expect(
			ruleValidityIssue(rule({ path: '/repos/{owner}', match_mode: 'prefix' })),
		).toBeNull();
	});

	it('leaves a rule with no path constraint alone', () => {
		expect(ruleValidityIssue(rule({ path: null, match_mode: 'regex' }))).toBeNull();
	});
});

describe('hasTemplatePlaceholder', () => {
	it('separates OpenAPI placeholders from regex quantifiers', () => {
		expect(hasTemplatePlaceholder('/repos/{owner}/{repo}')).toBe(true);
		expect(hasTemplatePlaceholder('/v1/charges')).toBe(false);
		expect(hasTemplatePlaceholder('/x{2,5}/y')).toBe(false);
	});
});
