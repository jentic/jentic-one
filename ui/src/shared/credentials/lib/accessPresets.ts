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
export type RulesPreset = 'all' | 'read' | 'custom';

/** How far a credential's scope reaches — what "Allow all" spans. */
export type ScopeReach = ReturnType<typeof apiScopeReach>;

export interface PresetOption {
	value: RulesPreset;
	label: string;
	description: string;
}

/**
 * What "Allow all operations" reaches: the rule is `path ".*"` on the whole
 * credential, so it spans everything the credential's scope covers — an
 * unpinned credential's future versions and, vendor-wide, every API of the
 * vendor (the broker resolves a binding through `credential_covers`).
 */
export const ALLOW_ALL_DESCRIPTION: Record<ScopeReach, string> = {
	pinned: 'Every operation of this API, any method.',
	'any-version':
		'Every operation of every version this credential covers — including versions added later — any method.',
	'vendor-wide':
		'Every operation of every API and version this credential covers — including ones added later — any method.',
};

/** The presets after Allow all — the same whatever the credential covers. */
const PRESET_REST: PresetOption[] = [
	{
		value: 'read',
		label: 'Read-only (GET only)',
		description: 'GET requests only — nothing that changes data.',
	},
	{
		value: 'custom',
		label: 'Custom rules',
		description: 'Write your own allow / deny rules, evaluated in order.',
	},
];

export function presetOptions(reach: ScopeReach): PresetOption[] {
	return [
		{ value: 'all', label: 'Allow all operations', description: ALLOW_ALL_DESCRIPTION[reach] },
		...PRESET_REST,
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

/** The read-only preset: any path, GET only. */
function readOnlyRule(): PermissionRuleInput {
	return {
		effect: 'allow' as PermissionRuleInput['effect'],
		methods: ['GET'],
		path: null,
		operations: null,
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
 */
export function rulesForPreset(
	preset: RulesPreset | null,
	customRules: readonly PermissionRule[],
): PermissionRuleInput[] | null {
	if (preset === 'all') return [cleanPermissionRule(allowAllRule())];
	if (preset === 'read') return [cleanPermissionRule(readOnlyRule())];
	if (preset === 'custom') {
		const wire = customRules.map(toWireRule);
		return wire.length > 0 && !wire.some(isEmptyAllowRule) ? wire : null;
	}
	return null;
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
