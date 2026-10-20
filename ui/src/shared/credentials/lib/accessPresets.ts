/**
 * Access presets — the "What can this agent call?" choice every bind surface
 * offers (the workspace bind dialog and the Agents tab's Add-APIs queue), and
 * the rules each preset writes. A binding is default-deny, so these are the
 * rules that decide what a freshly bound agent may call.
 */
import {
	allowAllRule,
	cleanPermissionRule,
	isEmptyAllowRule,
	type PermissionRuleInput,
} from '@/shared/ui';
import type { PermissionRule } from '@/shared/credentials/api/vendors-types';
import type { apiScopeReach } from '@/shared/credentials/lib/apiIdentity';

/** The access choice; `null` until one is made. */
export type RulesPreset = 'read' | 'read-ask' | 'all' | 'custom';

/** How far a credential's scope reaches — what "Full access" spans. */
export type ScopeReach = ReturnType<typeof apiScopeReach>;

export interface PresetOption {
	value: RulesPreset;
	label: string;
	description: string;
}

/**
 * What "Full access" reaches: the rule is `path ".*"` on the whole
 * credential, so it spans everything the credential's scope covers — an
 * unpinned credential's future versions and, vendor-wide, every API of the
 * vendor (the broker resolves a binding through `credential_covers`).
 */
export const ALLOW_ALL_DESCRIPTION: Record<ScopeReach, string> = {
	pinned: 'Any method, any path',
	'any-version': 'Any method, every version — incl. later ones',
	'vendor-wide': 'Every API this credential covers, incl. later ones',
};

/** The presets, least access first. */
export function presetOptions(reach: ScopeReach): PresetOption[] {
	return [
		// Read only stays GET only (no HEAD), so saved rules keep their meaning.
		{ value: 'read', label: 'Read only', description: 'GET requests only' },
		{
			value: 'read-ask',
			label: 'Read only, ask before anything else',
			description: 'GET requests run; any other call waits for approval',
		},
		{ value: 'all', label: 'Full access', description: ALLOW_ALL_DESCRIPTION[reach] },
		{
			value: 'custom',
			label: 'Custom rules',
			description: 'By action or by path, in order',
		},
	];
}

/** The coverage caveat under the presets, or null for a pinned credential. */
export function coverageNote(reach: ScopeReach): string | null {
	if (reach === 'vendor-wide') {
		return 'This credential covers every API of its vendor, in every version — these rules apply to all of them, including ones added later.';
	}
	if (reach === 'any-version') {
		return 'This credential covers every version of this API — these rules apply to future versions too.';
	}
	return null;
}

/** The effects a "By action" choice saves as (an unset action is in no rule). */
export type ActionEffect = 'allow' | 'require-approval' | 'deny';

/**
 * A "By action" rule: an allow, ask or deny constrained only by `operations`
 * (no method, no path) — what the By action list authors.
 */
export function isActionRule(rule: PermissionRule): boolean {
	return (
		(rule.effect === 'allow' || rule.effect === 'require-approval' || rule.effect === 'deny') &&
		(rule.operations?.length ?? 0) > 0 &&
		!rule.methods?.length &&
		!rule.path
	);
}

export function actionRule(operations: string[], effect: ActionEffect = 'allow'): PermissionRule {
	return { effect, methods: null, path: null, operations };
}

/** The By action list's choices, by operation id; an id in none is unset. */
export interface ActionGroups {
	allow: string[];
	ask: string[];
	deny: string[];
}

const GROUP_OF: Record<ActionEffect, keyof ActionGroups> = {
	allow: 'allow',
	'require-approval': 'ask',
	deny: 'deny',
};

/**
 * Custom rules split by which editor authors them: the path rules, and the
 * action rules as the By action list's Allow, Ask and Deny sets.
 *
 * Rules saved in an older shape still load: a single allow rule sets its
 * actions to Allow, several rules of one effect merge, and an id in more than
 * one action rule takes the effect of the earliest (first match wins).
 *
 * `denyMovesAhead` flags a Deny action rule found AFTER a path rule (only
 * possible from manual edits or the API): it loads as Deny, but saving moves it
 * ahead of the path rules, where it blocks those actions even when a path rule
 * allows them — a change of meaning the editor tells the operator about.
 */
export function splitCustomRules(rules: readonly PermissionRule[]): {
	pathRules: PermissionRule[];
	actionRules: PermissionRule[];
	groups: ActionGroups;
	denyMovesAhead: boolean;
} {
	const seen = new Set<string>();
	const groups: ActionGroups = { allow: [], ask: [], deny: [] };
	let pathSeen = false;
	let denyMovesAhead = false;
	for (const r of rules) {
		if (!isActionRule(r)) {
			pathSeen = true;
			continue;
		}
		if (r.effect === 'deny' && pathSeen) denyMovesAhead = true;
		for (const id of r.operations ?? []) {
			if (seen.has(id)) continue;
			seen.add(id);
			groups[GROUP_OF[r.effect as ActionEffect]].push(id);
		}
	}
	return {
		pathRules: rules.filter((r) => !isActionRule(r)),
		actionRules: rules.filter(isActionRule),
		groups,
		denyMovesAhead,
	};
}

/**
 * The By action choices as the rules they save, around the path rules:
 *
 *   Deny action rule → the path rules → Ask action rule → Allow action rule
 *
 * Deny goes first so denying an action blocks it even where a path rule allows
 * it. Ask and Allow go after, so the path rules keep deciding first (and the
 * dry run's numbers stay put); Ask before Allow is a safeguard — the sets are
 * disjoint. An effect with no actions saves no rule, and an id is kept in one
 * set only (Deny, then Ask, then Allow).
 */
export function joinCustomRules(
	pathRules: readonly PermissionRule[],
	groups: ActionGroups,
): PermissionRule[] {
	const taken = new Set<string>();
	const pick = (ids: readonly string[]): string[] =>
		[...new Set(ids)].filter((id) => !taken.has(id) && (taken.add(id), true));
	const deny = pick(groups.deny);
	const ask = pick(groups.ask);
	const allow = pick(groups.allow);
	return [
		...(deny.length ? [actionRule(deny, 'deny')] : []),
		...pathRules,
		...(ask.length ? [actionRule(ask, 'require-approval')] : []),
		...(allow.length ? [actionRule(allow, 'allow')] : []),
	];
}

/** The read-only preset: any path, GET only. */
function readOnlyRule(): PermissionRuleInput {
	return {
		effect: 'allow' as PermissionRuleInput['effect'],
		methods: ['GET'],
		path: null,
		operations: null,
	};
}

/** The catch-all Ask: any method on any path (`.*` as a regex — Ask needs a condition). */
function askAnythingRule(): PermissionRuleInput {
	return {
		effect: 'require-approval' as PermissionRuleInput['effect'],
		methods: null,
		path: '.*',
		match_mode: 'regex' as NonNullable<PermissionRuleInput['match_mode']>,
		operations: null,
	};
}

/** The rules each fixed preset saves, in order. */
function presetRules(preset: Exclude<RulesPreset, 'custom'>): PermissionRuleInput[] {
	switch (preset) {
		case 'all':
			return [cleanPermissionRule(allowAllRule())];
		case 'read':
			return [cleanPermissionRule(readOnlyRule())];
		case 'read-ask':
			// GET is decided first; everything else falls through to the Ask.
			return [cleanPermissionRule(readOnlyRule()), cleanPermissionRule(askAnythingRule())];
	}
}

const FIXED_PRESETS: readonly Exclude<RulesPreset, 'custom'>[] = ['read', 'read-ask', 'all'];

/** A rule's conditions, normalised so a saved rule compares equal to a preset's. */
function ruleKey(rule: PermissionRuleInput | PermissionRule): string {
	const r = cleanPermissionRule(rule as unknown as PermissionRuleInput);
	const mode = r.match_mode ?? 'regex';
	return JSON.stringify({
		e: r.effect,
		m: [...(r.methods ?? [])].map((x) => x.toUpperCase()).sort(),
		p: r.path ?? null,
		mm: r.path ? mode : null,
		o: [...(r.operations ?? [])].sort(),
	});
}

/**
 * The preset saved rules came from, so a binding reopens on the choice that
 * made it: the fixed preset whose rules they are exactly (in order), else
 * `custom` — or null for no rules at all.
 */
export function presetForRules(
	rules: readonly (PermissionRuleInput | PermissionRule)[],
): RulesPreset | null {
	if (rules.length === 0) return null;
	const keys = rules.map(ruleKey).join('\n');
	for (const preset of FIXED_PRESETS) {
		if (presetRules(preset).map(ruleKey).join('\n') === keys) return preset;
	}
	return 'custom';
}

/**
 * The access draft saved rules reopen as: their preset, and — for Custom —
 * the rules themselves, in the editor's shape.
 */
export function draftForRules(rules: readonly (PermissionRuleInput | PermissionRule)[]): {
	preset: RulesPreset | null;
	customRules: PermissionRule[];
} {
	const preset = presetForRules(rules);
	return {
		preset,
		customRules: preset === 'custom' ? rules.map((r) => r as unknown as PermissionRule) : [],
	};
}

/** A rules-editor rule in the wire shape (same fields; enum types differ). */
function toWireRule(rule: PermissionRule): PermissionRuleInput {
	return cleanPermissionRule(rule as unknown as PermissionRuleInput);
}

/** A wire rule in the rules editor's (and the local matcher's) shape. */
function toEditorShape(rule: PermissionRuleInput): PermissionRule {
	return rule as unknown as PermissionRule;
}

/**
 * The rules a preset writes, in the wire shape — or null while the choice is
 * incomplete (no preset yet, or Custom with no rules / an empty allow row).
 * An action rule carries `operations`, so it is never an empty allow row.
 */
export function rulesForPreset(
	preset: RulesPreset | null,
	customRules: readonly PermissionRule[],
): PermissionRuleInput[] | null {
	if (preset === null) return null;
	if (preset !== 'custom') return presetRules(preset);
	const wire = customRules.map(toWireRule);
	return wire.length > 0 && !wire.some(isEmptyAllowRule) ? wire : null;
}

/**
 * The ordered list a dry run evaluates for the current choice — exactly what
 * {@link rulesForPreset} would save, in the matcher's shape. An incomplete
 * Custom draft still evaluates as typed (so a half-written list can be probed);
 * no preset at all is no rules, i.e. default deny.
 */
export function draftRulesForPreset(
	preset: RulesPreset | null,
	customRules: readonly PermissionRule[],
): PermissionRule[] {
	if (preset === 'custom') return customRules.map((r) => toEditorShape(toWireRule(r)));
	return (rulesForPreset(preset, customRules) ?? []).map(toEditorShape);
}
