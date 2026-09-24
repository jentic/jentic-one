import { describe, expect, it } from 'vitest';
import { evaluateRules } from '@/shared/credentials/lib/rule-matcher';
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
