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
import { evaluateRules } from '@/shared/credentials/lib/rule-matcher';

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
 * * ``regex`` — structural segment-wise intersection for patterns whose
 *   slashes are plain top-level separators (the overwhelmingly common
 *   authoring shape), falling back to deterministic (seeded + memoized)
 *   sampling for exotic patterns. See ``regexCanIntersectTemplate``.
 */
export function ruleAppliesToTemplate(rule: PermissionRule, template: string): boolean {
	if (rule.path == null || rule.path === '') return true;
	const mode = rule.match_mode ?? 'regex';
	const templateRe = templateToRegex(template);
	if (mode === 'exact') return templateRe.test(rule.path);
	if (mode === 'prefix') return prefixCanApply(rule.path, template);
	// regex
	return regexCanIntersectTemplate(rule.path, template, templateRe);
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

// ---------------------------------------------------------------------------
// Regex ∩ template intersection
// ---------------------------------------------------------------------------

const REGEX_TRIALS = 20;

// Verdicts are memoized per (rule pattern, template) so repeated renders
// re-use the answer instead of recomputing (and so the sampling fallback
// can never flip a verdict between renders).
const INTERSECT_CACHE_MAX = 2000;
const intersectCache = new Map<string, boolean>();

/** FNV-1a string hash — seeds the deterministic PRNG below. */
function hashString(s: string): number {
	let h = 2166136261;
	for (let i = 0; i < s.length; i++) {
		h ^= s.charCodeAt(i);
		h = Math.imul(h, 16777619);
	}
	return h >>> 0;
}

/** mulberry32 — tiny deterministic PRNG (seed → [0, 1) stream). */
function mulberry32(seed: number): () => number {
	let a = seed >>> 0;
	return () => {
		a = (a + 0x6d2b79f5) | 0;
		let t = Math.imul(a ^ (a >>> 15), 1 | a);
		t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
		return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
	};
}

/**
 * A RandExp generator whose randomness is seeded from ``seedKey`` — the
 * same (pattern, template) pair always yields the same sample sequence,
 * so sampling-based verdicts are stable across renders.
 */
function seededRandExp(re: RegExp, seedKey: string, max: number = 8): RandExp {
	const gen = new RandExp(re);
	// Cap generated length so pathological patterns don't wedge the loop
	// on arbitrarily long outputs.
	gen.max = max;
	const rng = mulberry32(hashString(seedKey));
	gen.randInt = (from: number, to: number) => from + Math.floor(rng() * (to - from + 1));
	return gen;
}

/** Strip a leading ``^`` / trailing unescaped ``$`` anchor pair. */
function stripAnchors(source: string): string {
	let s = source;
	while (s.startsWith('^')) s = s.slice(1);
	while (s.endsWith('$') && !s.endsWith('\\$')) s = s.slice(0, -1);
	return s;
}

/**
 * Split a regex SOURCE into per-path-segment fragments at top-level
 * literal ``/`` separators (outside classes and groups; ``\/`` counts as
 * a literal slash too). Returns ``null`` when the pattern can't be
 * segmented safely — a ``/`` inside a class or group, or a top-level
 * alternation — in which case the caller falls back to sampling.
 */
function splitRegexSegments(source: string): string[] | null {
	const segments: string[] = [];
	let current = '';
	let depth = 0;
	let inClass = false;
	for (let i = 0; i < source.length; i++) {
		const c = source[i];
		if (c === '\\') {
			const next = source[i + 1] ?? '';
			if (next === '/' && depth === 0 && !inClass) {
				segments.push(current);
				current = '';
				i++;
				continue;
			}
			current += c + next;
			i++;
			continue;
		}
		if (inClass) {
			if (c === '/') return null; // '/' inside a class — unsafe to segment.
			if (c === ']') inClass = false;
			current += c;
			continue;
		}
		if (c === '[') {
			inClass = true;
			current += c;
			continue;
		}
		if (c === '(') {
			depth++;
			current += c;
			continue;
		}
		if (c === ')') {
			depth--;
			current += c;
			continue;
		}
		if (c === '|' && depth === 0) return null; // top-level alternation.
		if (c === '/') {
			if (depth !== 0) return null; // '/' inside a group — spans segments.
			segments.push(current);
			current = '';
			continue;
		}
		current += c;
	}
	segments.push(current);
	return segments;
}

function tryCompileAnchored(fragment: string): RegExp | null {
	try {
		return new RegExp(`^(?:${fragment})$`);
	} catch {
		return null;
	}
}

// Slash-free probe strings a segment fragment is tested against when it
// isn't a plain literal. Deliberately small + generic — covers the common
// authoring shapes ([a-z-]+, \d+, .*, .+, \w+, version-ish literals).
const SEGMENT_PROBES: readonly string[] = [
	'a',
	'x',
	'0',
	'1',
	'example',
	'example-name',
	'hello-world',
	'v1',
	'v1.0',
];

/**
 * Can ``fragment`` (a slash-free regex source) fully match at least one
 * NON-EMPTY, slash-free string (i.e. intersect ``[^/]+``)? Literal
 * fragments are their own witness; everything else is probed with a
 * small fixed set and then seeded RandExp samples.
 */
function fragmentMatchesSomeSegment(fragment: string, seedKey: string): boolean {
	const re = tryCompileAnchored(fragment);
	if (re == null) return false;
	// A pure literal fragment is its own witness.
	if (/^[\w.~-]+$/.test(fragment)) return re.test(fragment);
	for (const probe of SEGMENT_PROBES) {
		if (re.test(probe)) return true;
	}
	try {
		const gen = seededRandExp(new RegExp(fragment), seedKey, 6);
		for (let i = 0; i < 10; i++) {
			const s = gen.gen();
			if (s.length > 0 && !s.includes('/') && re.test(s)) return true;
		}
	} catch {
		// unparseable fragment in isolation — fall through to "no".
	}
	return false;
}

/**
 * Structural segment-wise intersection of an anchored rule regex with an
 * op template. Returns ``true``/``false`` when the pattern could be
 * segmented safely, ``null`` when the caller must fall back to sampling.
 */
function structuralIntersect(rulePattern: string, template: string): boolean | null {
	const ruleSegs = splitRegexSegments(stripAnchors(rulePattern));
	if (ruleSegs == null) return null;
	const tParts = splitPath(template);
	// More top-level slashes than the template has segments — placeholders
	// are single-segment, so the rule can never fully match an instance.
	if (ruleSegs.length > tParts.length) return false;
	// When the rule has FEWER segments than the template, some fragment
	// must consume slashes to cover the extra segments. If no fragment
	// can even contain a '/', that's impossible — conclusive miss. The
	// walk below only models the common shape where the LAST fragment
	// spans (e.g. ``/repos/.*``); a miss from it is therefore not
	// conclusive — a middle ``.*`` could span instead — so in the
	// shorter case failures return ``null`` (fall back to sampling)
	// rather than a hard "no". With equal segment counts the alignment
	// is forced (each fragment must stay slash-free to keep the total
	// slash count right), so both verdicts are exact.
	const equalLength = ruleSegs.length === tParts.length;
	if (!equalLength && !ruleSegs.some(fragmentCanContainSlash)) return false;
	const failVerdict = equalLength ? false : null;
	for (let i = 0; i < ruleSegs.length; i++) {
		const isLastRuleSeg = i === ruleSegs.length - 1;
		const ruleSeg = ruleSegs[i];
		if (isLastRuleSeg && !equalLength) {
			// The final rule fragment must span the REMAINING template
			// segments (slashes included) — e.g. the ``.*`` in ``/repos/.*``
			// covering ``{owner}/{repo}``. Probe with a concrete instance of
			// the remainder, then with seeded samples of the fragment.
			const remainderTemplate = tParts.slice(i).join('/');
			const remainderProbe = tParts
				.slice(i)
				.map((seg) => (isPlaceholder(seg) ? `example-${seg.slice(1, -1)}` : seg))
				.join('/');
			const segRe = tryCompileAnchored(ruleSeg);
			if (segRe == null) return failVerdict;
			if (segRe.test(remainderProbe)) return true;
			const remainderRe = templateToRegex(remainderTemplate);
			try {
				const gen = seededRandExp(
					new RegExp(ruleSeg),
					`${rulePattern}|${template}|tail`,
					12,
				);
				for (let t = 0; t < REGEX_TRIALS; t++) {
					if (remainderRe.test(gen.gen())) return true;
				}
			} catch {
				return failVerdict;
			}
			return failVerdict;
		}
		const tSeg = tParts[i];
		if (isPlaceholder(tSeg)) {
			// Placeholder segment — the rule fragment must intersect [^/]+.
			if (!fragmentMatchesSomeSegment(ruleSeg, `${rulePattern}|${template}|${i}`)) {
				return failVerdict;
			}
			continue;
		}
		// Fixed segment — the rule fragment must accept the literal.
		const segRe = tryCompileAnchored(ruleSeg);
		if (segRe == null || !segRe.test(tSeg)) return failVerdict;
	}
	return true;
}

/**
 * Heuristic: could ``fragment`` conceivably match a string containing a
 * ``/``? A fragment built purely from literals and character classes
 * that exclude ``/`` cannot; anything containing ``.``, ``\D``, ``\S``,
 * ``\W`` or a negated class without ``/`` might. Errs on the side of
 * ``true`` (callers use ``false`` to upgrade a miss to a hard "no").
 */
function fragmentCanContainSlash(fragment: string): boolean {
	// ``.`` matches '/', as do \D \S \W and negated classes that don't
	// re-exclude '/'. A conservative scan: any of those tokens present →
	// assume it can.
	if (/(?<!\\)\./.test(fragment)) return true;
	if (/\\[DSW]/.test(fragment)) return true;
	// Negated classes: assume they can match '/' unless '/' is listed.
	const classRe = /\[\^([^\]]*)\]/g;
	let m: RegExpExecArray | null;
	while ((m = classRe.exec(fragment)) != null) {
		if (!m[1].includes('/')) return true;
	}
	return false;
}

/**
 * Does the rule regex share any concrete request path with the template
 * regex? Prefers a structural segment-wise intersection (rule fragment
 * per template segment, ``[^/]+`` per ``{placeholder}``), which is exact
 * for the common "slashes are separators" authoring shape — including
 * multi-segment tails like ``/repos/.*`` against
 * ``/repos/{owner}/{repo}``. Patterns that can't be segmented safely
 * (slash inside a class/group, top-level alternation) fall back to
 * probing template-derived instances and then seeded RandExp sampling.
 * All paths are deterministic (seeded per (rule, template)) and the
 * verdict is memoized, so it can never flip between renders.
 */
function regexCanIntersectTemplate(
	rulePattern: string,
	template: string,
	templateRe: RegExp,
): boolean {
	const cacheKey = `${rulePattern}|${template}`;
	const cached = intersectCache.get(cacheKey);
	if (cached !== undefined) return cached;
	const verdict = computeRegexIntersect(rulePattern, template, templateRe);
	if (intersectCache.size >= INTERSECT_CACHE_MAX) intersectCache.clear();
	intersectCache.set(cacheKey, verdict);
	return verdict;
}

function computeRegexIntersect(rulePattern: string, template: string, templateRe: RegExp): boolean {
	let ruleRe: RegExp;
	try {
		ruleRe = new RegExp(rulePattern);
	} catch {
		return false; // Malformed rule regex — already flagged elsewhere.
	}
	const structural = structuralIntersect(rulePattern, template);
	if (structural !== null) return structural;
	// Fallback 1: concrete instances of the TEMPLATE tested against the
	// rule (anchored, mirroring the enforce-time full-match semantics) —
	// catches broad rules whose random samples would rarely hit the
	// template shape.
	const anchoredRule = tryCompileAnchored(stripAnchors(rulePattern));
	if (anchoredRule != null) {
		for (const probe of templateProbes(template)) {
			if (anchoredRule.test(probe)) return true;
		}
	}
	// Fallback 2: seeded RandExp samples of the rule tested against the
	// template regex.
	const gen = seededRandExp(ruleRe, `${rulePattern}|${template}`, 12);
	for (let i = 0; i < REGEX_TRIALS; i++) {
		if (templateRe.test(gen.gen())) return true;
	}
	return false;
}

/** A few concrete instances of a template (placeholders → sample values). */
function templateProbes(template: string): string[] {
	const parts = splitPath(template);
	const fills = ['example', 'a', '1'];
	return fills.map((fill) =>
		parts.map((seg) => (isPlaceholder(seg) ? `${fill}-${seg.slice(1, -1)}` : seg)).join('/'),
	);
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
	// regex — sample via seeded randexp (deterministic per (rule,
	// template) so examples don't shuffle between renders), keeping only
	// strings the template accepts. Template-derived probes come first so
	// broad rules (whose random samples rarely land in the template
	// shape) still produce an example.
	let ruleRe: RegExp;
	try {
		ruleRe = new RegExp(rule.path);
	} catch {
		return [];
	}
	const out = new Set<string>();
	const anchoredRule = tryCompileAnchored(stripAnchors(rule.path));
	if (anchoredRule != null) {
		for (const probe of templateProbes(template)) {
			if (out.size >= limit) break;
			if (anchoredRule.test(probe)) out.add(probe);
		}
	}
	const gen = seededRandExp(ruleRe, `${rule.path}|${template}|examples`);
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

// ---------------------------------------------------------------------------
// Per-op coverage classification (partial-allow visibility)
// ---------------------------------------------------------------------------

/**
 * Verdict for an op given the current rule set. ``partial`` means some
 * concrete instances of the op template are allowed and others denied
 * — the ops preview surfaces that state so the user isn't misled into
 * thinking a rule affects the whole op when it only covers a slice.
 */
export type OpCoverageVerdict = 'allow' | 'deny' | 'partial';

export interface OpCoverage {
	verdict: OpCoverageVerdict;
	// Up to a few concrete allowed sample paths (empty for ``deny``).
	allowedSamples: readonly string[];
	// Up to a few concrete denied sample paths (empty for ``allow``).
	deniedSamples: readonly string[];
}

/**
 * Sample concrete request paths from an op template — the enumeration
 * feeds the coverage classifier below. For each ``{placeholder}`` we
 * combine:
 * * a generic value (``example-<name>``) so we always cover the "no
 *   specific value is authorised" case, and
 * * every literal segment that appears at the same position in some
 *   rule's path (so a rule constraining owner to ``jentic`` also
 *   generates ``/repos/jentic/…`` samples).
 *
 * The Cartesian product is capped to keep evaluation cheap; ordering
 * biases toward samples that include rule-derived values so partial
 * verdicts hit meaningful splits (allow via rule vs default deny) as
 * early as possible.
 */
export function sampleTemplatePaths(
	template: string,
	rules: readonly PermissionRule[],
	limit: number = 8,
): string[] {
	const parts = template.split('/');
	const placeholderNames: (string | null)[] = parts.map((p) =>
		/^\{[^}]+\}$/.test(p) ? p.slice(1, -1) : null,
	);
	if (!placeholderNames.some((n) => n != null)) {
		// No placeholders — the template is its own concrete path.
		return [template];
	}

	// Per-position candidate value sets. Placeholder positions get a
	// generic + any rule-derived candidates; fixed positions carry the
	// template's own literal.
	const perPosition: string[][] = parts.map((seg, i) => {
		const name = placeholderNames[i];
		if (name == null) return [seg];
		const candidates = new Set<string>([`example-${name}`]);
		for (const rule of rules) {
			if (!rule.path) continue;
			const rParts = rule.path.split('/');
			if (i >= rParts.length) continue;
			const rSeg = rParts[i];
			// Only literal rule segments contribute — placeholders in the
			// rule are already covered by the generic value.
			if (/^\{[^}]+\}$/.test(rSeg) || rSeg.length === 0) continue;
			candidates.add(rSeg);
		}
		return Array.from(candidates);
	});

	// Cartesian product, capped. Interleave generic + rule-derived so a
	// small ``limit`` still hits both branches.
	let combos: string[][] = [[]];
	for (const segCandidates of perPosition) {
		const next: string[][] = [];
		for (const combo of combos) {
			for (const cand of segCandidates) {
				next.push([...combo, cand]);
				if (next.length >= limit * 4) break;
			}
			if (next.length >= limit * 4) break;
		}
		combos = next;
	}
	return combos.slice(0, limit).map((c) => c.join('/'));
}

/**
 * Classify how the rule set covers an op template. Runs enforce-time
 * evaluation (``evaluateRules``, now placeholder-aware) over a set of
 * concrete samples drawn from the template. Bucket:
 *
 * * ``allow`` — every sample is allowed. Op is fully covered.
 * * ``deny`` — every sample is denied. Op has no path in.
 * * ``partial`` — some allowed, some denied. Op has a narrow allow
 *   surface — user sees "e.g. X allowed / e.g. Y denied" on expand.
 *
 * Sample count is bounded, so ``allow``/``deny`` are best-effort
 * (a pathological rule set could sneak an outlier past the samples).
 * In practice the biased sampler in ``sampleTemplatePaths`` hits any
 * literal a rule references, so realistic policy patterns classify
 * correctly.
 */
export function classifyOpCoverage(
	rules: readonly PermissionRule[],
	op: { method: string; path: string; operation_id: string | null },
	maxExamples: number = 2,
): OpCoverage {
	const samples = sampleTemplatePaths(op.path, rules);
	const allowedSamples: string[] = [];
	const deniedSamples: string[] = [];
	for (const sample of samples) {
		const allowed = evaluateRules(rules, {
			method: op.method,
			path: sample,
			operation_id: op.operation_id,
		});
		if (allowed) {
			if (allowedSamples.length < maxExamples) allowedSamples.push(sample);
		} else {
			if (deniedSamples.length < maxExamples) deniedSamples.push(sample);
		}
	}
	if (allowedSamples.length > 0 && deniedSamples.length === 0) {
		return { verdict: 'allow', allowedSamples: [], deniedSamples: [] };
	}
	if (deniedSamples.length > 0 && allowedSamples.length === 0) {
		return { verdict: 'deny', allowedSamples: [], deniedSamples: [] };
	}
	return { verdict: 'partial', allowedSamples, deniedSamples };
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
