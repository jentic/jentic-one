// Template-aware rule matcher for the UI's operation-impact preview.
//
// The backend enforce path (``broker/repos/rule_evaluator.py``) sees the
// CONCRETE request path (e.g. ``/repos/octocat/hello-world``) — its
// matcher is a plain string comparison via ``shared/permissions/matching.py``
// and doesn't need any template awareness.
//
// The UI preview is different: it evaluates rules against op TEMPLATES
// from the OpenAPI catalogue (e.g. ``/repos/{owner}/{repo}``). If we
// re-used the enforce-time matcher here, an ``exact`` rule for
// ``/repos/octocat/hello-world`` would show "denied" against the
// ``/repos/{owner}/{repo}`` op even though the rule WOULD allow that
// exact call at runtime. To close that gap, this module converts each
// ``{name}`` placeholder in the template into a ``[^/]+`` segment and
// asks "does any concrete instance of this template satisfy the
// rule?" — which is the property the user actually cares about on the
// preview.
//
// Placeholder-to-regex conversion escapes literal segments so regex
// metacharacters in the template (unusual, but possible) don't leak
// into the derived pattern. Placeholders are single-segment (they
// don't span slashes) so we deliberately use ``[^/]+`` rather than
// ``.+`` — the enforced OpenAPI shape.
//
// Kept as a SEPARATE module from ``rule-matcher.ts`` so the Python
// parity test on the enforce-time matcher stays a pure identity check
// and this UI-only path can evolve without breaking that contract.

import RandExp from 'randexp';
import type { PermissionRule } from '@/shared/credentials/api/vendors-types';

/**
 * Convert an OpenAPI-style path template to a JS RegExp that matches any
 * concrete request path the template could yield. ``{name}`` becomes
 * ``[^/]+``; every other character is regex-escaped so slashes and dots
 * are matched literally.
 */
export function templateToRegex(template: string): RegExp {
	// Split on placeholders so we can escape the literal segments and
	// replace the placeholders in one pass.
	const parts = template.split(/(\{[^}]+\})/g);
	const source = parts.map((p) => (/^\{[^}]+\}$/.test(p) ? '[^/]+' : escapeRegex(p))).join('');
	return new RegExp(`^${source}$`);
}

function escapeRegex(s: string): string {
	return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * Does any concrete path derivable from ``template`` also satisfy the
 * rule's path constraint? Used by the ops preview to decide whether a
 * given op is affected by a rule. Method + operation-id constraints are
 * checked by the caller — this is path-only.
 *
 * Semantics per match_mode:
 * * ``null`` path — matches everything.
 * * ``exact`` — the rule's literal path must be a valid instance of the
 *   template regex.
 * * ``prefix`` — some concrete template instance must start with the
 *   rule's prefix. Segment-walk: fixed template segments must equal the
 *   prefix's segment at that index; placeholder segments accept anything.
 *   Trailing prefix segments past the template's length are treated as
 *   never matching (a longer prefix than the template can't apply).
 * * ``regex`` — probabilistic: generate up to ``REGEX_TRIALS`` samples
 *   from the rule regex and see if any land in the template regex.
 *   RandExp is deterministic when seeded, so this is stable across
 *   renders.
 */
export function ruleAppliesToTemplate(rule: PermissionRule, template: string): boolean {
	if (rule.path == null || rule.path === '') return true;
	const mode = rule.match_mode ?? 'regex';
	const templateRe = templateToRegex(template);
	if (mode === 'exact') return templateRe.test(rule.path);
	if (mode === 'prefix') return prefixCanApply(rule.path, template);
	// regex
	return regexCanIntersectTemplate(rule.path, templateRe);
}

/**
 * Segment-by-segment check that ``prefix`` is a valid opening slice of
 * some concrete instance of ``template``. A trailing partial segment on
 * the prefix (i.e. ``/repos/octo``) must be a prefix of the corresponding
 * template segment (or match anything if the template segment is a
 * placeholder).
 */
function prefixCanApply(prefix: string, template: string): boolean {
	const pParts = splitPath(prefix);
	const tParts = splitPath(template);
	if (pParts.length > tParts.length) return false;
	for (let i = 0; i < pParts.length; i++) {
		const tSeg = tParts[i];
		const pSeg = pParts[i];
		if (isPlaceholder(tSeg)) continue;
		// Last prefix segment might be partial — allow "startsWith" against
		// the fixed template segment. Non-last segments must equal exactly.
		if (i === pParts.length - 1) {
			if (!tSeg.startsWith(pSeg)) return false;
		} else if (tSeg !== pSeg) {
			return false;
		}
	}
	return true;
}

function splitPath(s: string): string[] {
	// Preserve leading-slash semantics: ``/a/b`` → ``["", "a", "b"]``.
	// We keep the empty leading segment so a bare ``/`` is treated
	// consistently, but for the prefix walk both sides carry the same
	// leading empty so it always matches.
	return s.split('/');
}

function isPlaceholder(segment: string): boolean {
	return /^\{[^}]+\}$/.test(segment);
}

/**
 * Randexp-based check: does the rule regex share any concrete string
 * with the template regex? Deterministic seed so the answer doesn't
 * jitter between renders.
 */
const REGEX_TRIALS = 20;

function regexCanIntersectTemplate(rulePattern: string, templateRe: RegExp): boolean {
	let ruleRe: RegExp;
	try {
		ruleRe = new RegExp(rulePattern);
	} catch {
		return false; // Malformed rule regex — already flagged elsewhere.
	}
	const gen = new RandExp(ruleRe);
	// Cap generated length so pathological patterns don't wedge the loop
	// on arbitrarily long outputs.
	gen.max = 8;
	for (let i = 0; i < REGEX_TRIALS; i++) {
		const candidate = gen.gen();
		if (templateRe.test(candidate)) return true;
	}
	return false;
}

/**
 * Generate up to ``limit`` example concrete paths that BOTH satisfy the
 * rule and are valid instances of the op template. Returns ``[]`` when
 * no examples can be found (e.g. rule regex doesn't intersect the
 * template, or randexp can't produce a hit within the trial budget).
 *
 * Callers should only invoke this when the rule NARROWS the template
 * (i.e. ``ruleNarrowsTemplate`` returns true); a null / catch-all path
 * doesn't have a meaningful concrete example — the op's own template
 * is already the answer.
 */
export function generateRuleExamples(
	rule: PermissionRule,
	template: string,
	limit: number = 3,
): string[] {
	if (rule.path == null || rule.path === '') return [];
	const mode = rule.match_mode ?? 'regex';
	const templateRe = templateToRegex(template);
	if (mode === 'exact') {
		return templateRe.test(rule.path) ? [rule.path] : [];
	}
	if (mode === 'prefix') {
		// Fill remaining placeholders with generic sample segments so the
		// user sees a concrete instance rather than the mixed
		// prefix + placeholder string.
		const example = fillPrefixExample(rule.path, template);
		return example ? [example] : [];
	}
	// regex — sample via randexp, keep only strings the template accepts.
	let ruleRe: RegExp;
	try {
		ruleRe = new RegExp(rule.path);
	} catch {
		return [];
	}
	const gen = new RandExp(ruleRe);
	gen.max = 8;
	const out = new Set<string>();
	for (let i = 0; i < REGEX_TRIALS && out.size < limit; i++) {
		const candidate = gen.gen();
		if (templateRe.test(candidate)) out.add(candidate);
	}
	return Array.from(out);
}

/**
 * True when the rule genuinely constrains what would otherwise be an
 * unrestricted template instance — i.e. the rule's path is more specific
 * than the template. Used to gate whether we render example paths on
 * ops-preview rows.
 */
export function ruleNarrowsTemplate(rule: PermissionRule, template: string): boolean {
	if (rule.path == null || rule.path === '') return false;
	// A prefix of ``/`` matches every path — not narrowing.
	if (rule.match_mode === 'prefix' && rule.path === '/') return false;
	if (!ruleAppliesToTemplate(rule, template)) return false;
	return true;
}

export interface TemplateEvaluation {
	allowed: boolean;
	// The first rule that matched the op (deny or allow). ``null`` when
	// nothing matched — that's default-deny territory.
	matchingRule: PermissionRule | null;
}

/**
 * First-match-wins evaluation of a rule set against an op described by
 * ``(method, path template, operation_id)``. Uses template-aware path
 * matching (see ``ruleAppliesToTemplate``) so a rule like ``exact:
 * /repos/foo/bar`` correctly matches an op with template
 * ``/repos/{owner}/{repo}``. Mirrors the effect resolution rules of the
 * enforce-time matcher; the divergence is only in HOW path matching is
 * performed.
 */
export function evaluateTemplateOp(
	rules: readonly PermissionRule[],
	op: { method: string; path: string; operation_id: string | null },
): TemplateEvaluation {
	for (const rule of rules) {
		// Skip condition-less allow rules (backend rejects saving them,
		// but paranoid mirror of the enforce-time matcher).
		if (
			rule.effect === 'allow' &&
			!(rule.methods && rule.methods.length > 0) &&
			(rule.path == null || rule.path === '') &&
			!(rule.operations && rule.operations.length > 0)
		) {
			continue;
		}
		if (rule.methods && rule.methods.length > 0) {
			const upper = rule.methods.map((m) => m.toUpperCase());
			if (!upper.includes(op.method.toUpperCase())) continue;
		}
		if (rule.operations && rule.operations.length > 0) {
			if (op.operation_id == null) continue;
			if (!rule.operations.includes(op.operation_id)) continue;
		}
		if (rule.path != null && rule.path !== '') {
			if (!ruleAppliesToTemplate(rule, op.path)) continue;
		}
		return { allowed: rule.effect === 'allow', matchingRule: rule };
	}
	return { allowed: false, matchingRule: null };
}

/** Fill the template's placeholders past the given prefix with generic sample values. */
function fillPrefixExample(prefix: string, template: string): string | null {
	const pParts = splitPath(prefix);
	const tParts = splitPath(template);
	if (pParts.length > tParts.length) return null;
	const out: string[] = [];
	for (let i = 0; i < tParts.length; i++) {
		if (i < pParts.length) {
			const tSeg = tParts[i];
			const pSeg = pParts[i];
			if (isPlaceholder(tSeg)) {
				// Placeholder overlapping with a prefix segment: use the
				// prefix's concrete value.
				out.push(pSeg);
			} else if (i === pParts.length - 1) {
				// Partial trailing segment on the prefix — extend to the
				// full template segment so the result is a real op path.
				out.push(tSeg);
			} else {
				out.push(pSeg);
			}
		} else {
			const tSeg = tParts[i];
			out.push(isPlaceholder(tSeg) ? placeholderSample(tSeg) : tSeg);
		}
	}
	return out.join('/');
}

function placeholderSample(placeholder: string): string {
	// ``{name}`` → ``<name>`` — visibly a placeholder to the user but
	// unambiguously a concrete string (no braces so the reader doesn't
	// confuse it with a template).
	const inner = placeholder.slice(1, -1);
	return `<${inner}>`;
}
