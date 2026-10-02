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
//   ``prefix`` (``startsWith`` for pure-literal, prefix-regex for
//   placeholder-bearing paths), ``exact`` (equality for pure-literal,
//   fullmatch-regex for placeholder-bearing paths).
// * A regex that fails to compile → fail-closed matcher (never matches).
// * A condition-less ``allow`` rule is skipped by the evaluator (should
//   have been rejected client-side; belt & braces).
// * First-match-wins; default-deny when nothing matches.
// * Exact / prefix paths bearing ``{name}`` placeholders compile to a
//   regex where each placeholder is ``[^/]+`` — same shape users see on
//   op templates. See ``templatePathToRegex``. Kept in lockstep with
//   Python ``matching.py::_template_path_to_regex``.

import type { PermissionRule } from '@/shared/credentials/api/vendors-types';

export type MatchMode = 'regex' | 'prefix' | 'exact';

interface PathMatcher {
	mode: MatchMode;
	literal: string | null;
	pattern: RegExp | null;
	never: boolean;
}

// ``{name}`` placeholder — single-segment (no slashes) OpenAPI style.
// Kept in step with the Python side's ``_PLACEHOLDER_RE``.
const PLACEHOLDER_RE = /\{[^}/]+\}/g;

// Nested-unbounded-quantifier catastrophic-backtracking guard —
// mirrors ``_REDOS_CATASTROPHIC_RE`` in the Python matcher. Kept
// in lockstep so a rule the server would reject at save time also
// surfaces as invalid in the rules editor's live preview (rather
// than shipping to the backend and failing 422). See the Python
// side for the shape rationale.
const REDOS_CATASTROPHIC_RE = /[+*][^)]*\)\s*[+*?]/;

function hasPlaceholders(path: string): boolean {
	return path.includes('{') && PLACEHOLDER_RE.test(path);
}

function escapeRegex(s: string): string {
	return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * Compile a placeholder-bearing exact/prefix path into a RegExp — each
 * ``{name}`` becomes ``[^/]+`` (single-segment wildcard), matching the
 * OpenAPI template shape the user sees on op definitions. Literal parts
 * are regex-escaped so slashes and dots are matched verbatim.
 */
function templatePathToRegex(path: string): RegExp {
	// Consume the source in one pass: everything between placeholder
	// hits is escaped literal, placeholders themselves become wildcards.
	// ``PLACEHOLDER_RE`` is a global regex so we reset its lastIndex to
	// keep this function pure across calls (the ``test`` call in
	// ``hasPlaceholders`` may have moved it).
	PLACEHOLDER_RE.lastIndex = 0;
	const parts = path.split(PLACEHOLDER_RE);
	const source = parts.map((seg, i) => (i === 0 ? '' : '[^/]+') + escapeRegex(seg)).join('');
	return new RegExp(source);
}

function compileMatcher(path: string | null | undefined, mode: MatchMode): PathMatcher | null {
	if (path == null) return null;
	if (path === '') {
		// Empty path is rejected by the Python side (``_check``) too. Mirror
		// by returning a never-match matcher so the rule is fail-closed.
		return { mode, literal: null, pattern: null, never: true };
	}
	if (mode === 'regex') {
		if (REDOS_CATASTROPHIC_RE.test(path)) {
			// Backend refuses this at save time; mirror the fail-closed
			// verdict so the ops-preview grid stays honest.
			return { mode: 'regex', literal: null, pattern: null, never: true };
		}
		try {
			return { mode, literal: null, pattern: new RegExp(path), never: false };
		} catch {
			return { mode: 'regex', literal: null, pattern: null, never: true };
		}
	}
	// exact / prefix with ``{name}`` placeholders compiles to a regex —
	// see docstring on ``templatePathToRegex``. Paths with no
	// placeholders stay on the pure-literal branch below so existing
	// behaviour is unchanged for the common case.
	if (hasPlaceholders(path)) {
		return { mode, literal: path, pattern: templatePathToRegex(path), never: false };
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

/**
 * Start-anchored partial match — mirrors ``re.match`` semantics on the
 * Python side so a placeholder-bearing prefix rule ``/repos/{owner}``
 * accepts ``/repos/octocat/anything``.
 */
function anchoredStartMatch(pattern: RegExp, s: string): boolean {
	const anchored = new RegExp(`^(?:${pattern.source})`, pattern.flags);
	return anchored.test(s);
}

function matcherMatches(m: PathMatcher, requestPath: string): boolean {
	if (m.never) return false;
	if (m.mode === 'regex') {
		if (m.pattern == null) return false;
		return fullMatch(m.pattern, requestPath);
	}
	// Exact / prefix: pattern set only when the authored path had
	// placeholders; otherwise we fall through to the pure-literal
	// comparison against ``literal``.
	if (m.pattern != null) {
		if (m.mode === 'prefix') return anchoredStartMatch(m.pattern, requestPath);
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

/**
 * Reason a rule can never match, or ``null`` if the rule looks well-formed.
 * The rules editor calls this per row so it can flag a rule whose pattern
 * would silently fail-closed — otherwise the user has no signal that the
 * rule isn't doing anything. Mirrors the fail-closed branches in
 * ``compileMatcher``.
 */
export type RuleValidityIssue = 'invalid-regex' | 'empty-regex' | 'unsafe-regex';

export function ruleValidityIssue(rule: PermissionRule): RuleValidityIssue | null {
	// The regex mode is where silent failure is most likely — an empty
	// pattern or an unparseable one both produce a matcher that will
	// never fire, and the user gets no feedback until they hit Continue
	// and observe the ops-preview grid stay red.
	if ((rule.match_mode ?? 'regex') !== 'regex') return null;
	if (rule.path == null) return null;
	if (rule.path === '') return 'empty-regex';
	if (REDOS_CATASTROPHIC_RE.test(rule.path)) return 'unsafe-regex';
	try {
		new RegExp(rule.path);
		return null;
	} catch {
		return 'invalid-regex';
	}
}
