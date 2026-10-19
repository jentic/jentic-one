/**
 * Reading an edit session's review: the combined diff between the binding's
 * access today and what approving the current revision leaves in place.
 * Pure helpers; the edit review renders them.
 */
import type {
	EditRuleView,
	PermissionRule,
	ReviewEdit,
} from '@/shared/credentials/api/vendors-types';

/** How one rule fares in the change. */
export interface RuleChange {
	kind: 'kept' | 'added' | 'removed';
	rule: EditRuleView;
	/** The change could let the agent do more (an added allow / Ask, a dropped deny). */
	widens: boolean;
}

/** A rule's comparable content (no id, ``match_mode`` defaulting to ``regex``). */
function ruleKey(rule: PermissionRule): string {
	return JSON.stringify([
		rule.effect,
		rule.methods ?? null,
		rule.path ?? null,
		rule.match_mode ?? 'regex',
		rule.operations ?? null,
	]);
}

const GRANTING: ReadonlySet<PermissionRule['effect']> = new Set(['allow', 'require-approval']);

/** The rule list today: the attached shared set's when there is one. */
export function currentRules(edit: ReviewEdit): EditRuleView[] {
	return edit.current.rule_set?.rules ?? edit.current.rules;
}

/**
 * The change rule by rule, in the result's order (first match wins), with the
 * rules it drops listed after.
 */
export function ruleChanges(edit: ReviewEdit): RuleChange[] {
	const before = currentRules(edit);
	const beforeKeys = new Set(before.map(ruleKey));
	const afterKeys = new Set(edit.result.rules.map(ruleKey));
	const changes: RuleChange[] = edit.result.rules.map((rule) => {
		const kept = beforeKeys.has(ruleKey(rule));
		return {
			kind: kept ? 'kept' : 'added',
			rule,
			widens: !kept && GRANTING.has(rule.effect),
		};
	});
	for (const rule of before) {
		if (!afterKeys.has(ruleKey(rule))) {
			changes.push({ kind: 'removed', rule, widens: rule.effect === 'deny' });
		}
	}
	return changes;
}

/** Scopes the result holds that the credential's grant does not. */
export function addedScopes(edit: ReviewEdit): string[] {
	const granted = new Set(edit.current.granted_scopes ?? []);
	return edit.result.scopes.filter((scope) => !granted.has(scope));
}

/** Whether approving switches a suspended binding back on. */
export function resumes(edit: ReviewEdit): boolean {
	return edit.current.suspended && !edit.result.suspended;
}

/** The rules to send on ``:confirm`` (the result's, without ids). */
export function rulesToConfirm(rules: readonly EditRuleView[]): PermissionRule[] {
	return rules.map(({ id: _id, ...rule }) => rule);
}

/** "GET, POST /v1/x (prefix)": one rule's match in words. */
export function describeRuleMatch(rule: PermissionRule): string {
	const methods = rule.methods?.length ? rule.methods.join(', ') : 'Any method';
	const path = rule.path ?? 'any path';
	const mode = rule.path ? ` (${rule.match_mode ?? 'regex'})` : '';
	const operations = rule.operations?.length ? `, operations ${rule.operations.join(', ')}` : '';
	return `${methods} ${path}${mode}${operations}`;
}
