import { describe, expect, it } from 'vitest';
import { evaluateRuleEffect, evaluateRules } from '@/shared/credentials/lib/rule-matcher';
import type { PermissionRule } from '@/shared/credentials/api/vendors-types';
import fixture from './rule-matcher-parity.json';

/**
 * Parity test — pins that the TS matcher agrees with the Python side.
 * The shared fixture (vendored here from ``tests/fixtures/rule-matcher-parity.json``;
 * the Docker UI build copies only ``ui/``, so no cross-boundary import) is
 * consumed by both. Any divergence fails CI in both places. See the
 * Python test at ``tests/unit/shared/test_rule_matcher_parity.py`` for
 * the sibling side.
 *
 * Do NOT edit the fixture to make a red test go green — fix the
 * divergence at the source (either the shared ``matching.py`` /
 * ``rule_evaluator.py`` or the TS ``rule-matcher.ts``).
 */

interface Case {
	name: string;
	rules: PermissionRule[];
	request: { method: string; path: string; operation_id: string | null };
	allowed: boolean;
}

describe('rule-matcher parity with Python side', () => {
	const cases = (fixture as { cases: Case[] }).cases;
	it.each(cases)('$name', ({ rules, request, allowed }) => {
		expect(evaluateRules(rules, request)).toBe(allowed);
	});
});

describe('evaluateRuleEffect', () => {
	const cases = (fixture as { cases: Case[] }).cases;
	it.each(cases)('resolves to allow exactly when the parity case allows: $name', (c) => {
		expect(evaluateRuleEffect(c.rules, c.request) === 'allow').toBe(c.allowed);
	});

	const req = { method: 'POST', path: '/v1/charges', operation_id: null };

	it('returns the first matching rule effect, holds included', () => {
		const rules: PermissionRule[] = [
			{ effect: 'require-approval', methods: ['POST'], path: null },
			{ effect: 'allow', methods: ['POST'], path: null },
		];
		expect(evaluateRuleEffect(rules, req)).toBe('require-approval');
		expect(evaluateRules(rules, req)).toBe(false);
	});

	it('defaults to deny and skips a condition-less require-approval rule', () => {
		expect(evaluateRuleEffect([], req)).toBe('deny');
		expect(
			evaluateRuleEffect([{ effect: 'require-approval', methods: null, path: null }], req),
		).toBe('deny');
	});
});
