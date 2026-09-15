// TS port of ``shared/permissions/matching.py`` +
// ``broker/repos/rule_evaluator.evaluate_rules``. Used by the connect-flow
// rules page to preview which operations a candidate rule set would
// allow / deny WITHOUT firing per-operation HTTP calls against
// ``:test``.
//
// Parity with the Python side is pinned by
// ``__tests__/rule-matcher.test.ts``, which consumes the shared JSON
// fixture at ``__fixtures__/rule-matcher-parity.json`` (also read by
// the Python side's ``test_matching_parity.py``). Any divergence in
// semantics between the two matchers fails CI on both sides.
//
// Semantics recap:
// * ``path is null``  → no path constraint (rule matches every path).
// * ``methods is null`` → no method constraint.
// * ``operations is null`` → no operation-id constraint.
// * Match modes: ``regex`` (JS full-match — see ``fullMatch`` below),
//   ``prefix`` (String#startsWith), ``exact`` (equality).
// * A regex that fails to compile → fail-closed matcher (never matches).
// * A condition-less ``allow`` rule is skipped by the evaluator (should
//   have been rejected client-side; belt & braces).
// * First-match-wins; default-deny when nothing matches.

import type { PermissionRule } from '@/shared/credentials/api/vendors-types';

export type MatchMode = 'regex' | 'prefix' | 'exact';

interface PathMatcher {
	mode: MatchMode;
	literal: string | null;
	pattern: RegExp | null;
	never: boolean;
}

function compileMatcher(path: string | null | undefined, mode: MatchMode): PathMatcher | null {
	if (path == null) return null;
	if (path === '') {
		// Empty path is rejected by the Python side (``_check``) too. Mirror
		// by returning a never-match matcher so the rule is fail-closed.
		return { mode, literal: null, pattern: null, never: true };
	}
	if (mode === 'regex') {
		try {
			return { mode, literal: null, pattern: new RegExp(path), never: false };
		} catch {
			return { mode: 'regex', literal: null, pattern: null, never: true };
		}
	}
	return { mode, literal: path, pattern: null, never: false };
}

// JS ``RegExp`` doesn't have a fullmatch primitive; anchor with ``^…$``
// only if the caller hasn't already anchored. Simpler + safe: wrap and
// let JS collapse the double anchors (``^^…$$`` behaves the same as
// ``^…$``).
function fullMatch(pattern: RegExp, s: string): boolean {
	const anchored = new RegExp(`^(?:${pattern.source})$`, pattern.flags);
	return anchored.test(s);
}

function matcherMatches(m: PathMatcher, requestPath: string): boolean {
	if (m.never) return false;
	if (m.mode === 'regex') {
		if (m.pattern == null) return false;
		return fullMatch(m.pattern, requestPath);
	}
	if (m.literal == null) return false;
	if (m.mode === 'prefix') return requestPath.startsWith(m.literal);
	return requestPath === m.literal;
}

interface CompiledRule {
	effect: 'allow' | 'deny';
	methods: Set<string> | null;
	path: PathMatcher | null;
	operations: Set<string> | null;
}

function compileRule(rule: PermissionRule): CompiledRule {
	return {
		effect: rule.effect,
		methods:
			rule.methods && rule.methods.length > 0
				? new Set(rule.methods.map((m) => m.toUpperCase()))
				: null,
		path: compileMatcher(rule.path, rule.match_mode ?? 'regex'),
		operations: rule.operations && rule.operations.length > 0 ? new Set(rule.operations) : null,
	};
}

function isConditionLess(r: CompiledRule): boolean {
	return r.methods == null && r.path == null && r.operations == null;
}

function ruleMatches(
	r: CompiledRule,
	req: { method: string; path: string; operation_id: string | null },
): boolean {
	if (r.methods != null && !r.methods.has(req.method.toUpperCase())) return false;
	if (r.path != null && !matcherMatches(r.path, req.path)) return false;
	if (r.operations != null) {
		if (req.operation_id == null) return false;
		if (!r.operations.has(req.operation_id)) return false;
	}
	return true;
}

/**
 * Evaluate an ordered rule list against a request triple. Returns
 * ``true`` iff a matching ``allow`` rule fires before any matching
 * ``deny``. Default-deny when nothing matches.
 */
export function evaluateRules(
	rules: readonly PermissionRule[],
	req: { method: string; path: string; operation_id: string | null },
): boolean {
	for (const raw of rules) {
		const compiled = compileRule(raw);
		if (isConditionLess(compiled) && compiled.effect === 'allow') continue;
		if (ruleMatches(compiled, req)) return compiled.effect === 'allow';
	}
	return false;
}
