/**
 * A binding rule as the words of a compact chip (the agent card's Access
 * column): the effect word, then what the rule scopes to.
 *
 * Each kind of condition reads in its own way — methods by name ("GET, POST"),
 * a path as itself, an action rule's operation ids as the actions they are
 * ("5 actions", or the one action's method and path). "any method" is said
 * only of a path rule that names no methods, never of an action rule: an
 * action already fixes its method.
 */
import {
	actionTooltipLines,
	actionsSummary,
	describeActions,
	type ActionCatalog,
} from '@/shared/credentials/lib/actionRules';

/** The rule fields a chip reads; matches the binding rule's wire shape. */
export interface ChipRule {
	effect: string;
	methods?: readonly string[] | null;
	path?: string | null;
	operations?: readonly string[] | null;
}

/** The word a rule chip leads with, per effect. */
const EFFECT_WORD: Record<string, string> = {
	allow: 'Allow',
	'require-approval': 'Ask',
	deny: 'Deny',
};

export interface RuleChipWords {
	/** "Allow" / "Ask" / "Deny". */
	effect: string;
	/** The effect's key for styling (`allow` / `ask` / `deny`, or an unknown one as-is). */
	effectKey: string;
	/** What the rule scopes to, e.g. `GET, POST /repos/` or `5 actions`. */
	scope: string;
	/** Readable lines for the chip's tooltip, after the headline. */
	details: string[];
}

const EFFECT_KEY: Record<string, string> = {
	allow: 'allow',
	'require-approval': 'ask',
	deny: 'deny',
};

/** How many actions a chip's tooltip names before "+N more". */
export const CHIP_TOOLTIP_ACTIONS = 5;

/** "Allow GET, POST /repos/*" — one rule as a chip's words. */
export function ruleChipWords(rule: ChipRule, catalog: ActionCatalog): RuleChipWords {
	const effectId = String(rule.effect);
	// An effect this chip doesn't know is named as it is, never passed off as allow.
	const effect = EFFECT_WORD[effectId] ?? effectId.charAt(0).toUpperCase() + effectId.slice(1);
	const effectKey = EFFECT_KEY[effectId] ?? effectId;
	const methods = rule.methods?.length ? rule.methods.join(', ') : null;
	const path = rule.path || null;
	const ops = rule.operations?.length ? rule.operations : null;

	if (ops) {
		const actions = describeActions(ops, catalog);
		const summary = actionsSummary(actions);
		// A rule narrowed by methods or path as well keeps them, ahead of its actions.
		const narrowing = [methods, path].filter(Boolean).join(' ');
		return {
			effect,
			effectKey,
			scope: narrowing ? `${narrowing} · ${summary}` : summary,
			details: actionTooltipLines(actions, CHIP_TOOLTIP_ACTIONS),
		};
	}
	const bits: string[] = [];
	if (methods) bits.push(methods);
	if (path) bits.push(methods ? path : `any method ${path}`);
	return {
		effect,
		effectKey,
		scope: bits.length > 0 ? bits.join(' ') : 'every request',
		details: [],
	};
}
