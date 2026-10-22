/**
 * Reading an edit session's review: the combined diff between the binding's
 * access today and what approving the current revision leaves in place.
 * Pure helpers; the edit review renders them.
 */
import type {
	EditConflict,
	EditRuleView,
	PermissionRule,
	ReviewEdit,
	SessionRevision,
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
 * ``before`` → ``after`` rule by rule, in ``after``'s order (first match
 * wins), with the rules it drops listed after.
 */
export function diffRules(
	before: readonly EditRuleView[],
	after: readonly EditRuleView[],
): RuleChange[] {
	const beforeKeys = new Set(before.map(ruleKey));
	const afterKeys = new Set(after.map(ruleKey));
	const changes: RuleChange[] = after.map((rule) => {
		const kept = beforeKeys.has(ruleKey(rule));
		return { kind: kept ? 'kept' : 'added', rule, widens: !kept && GRANTING.has(rule.effect) };
	});
	for (const rule of before) {
		if (!afterKeys.has(ruleKey(rule))) {
			changes.push({ kind: 'removed', rule, widens: rule.effect === 'deny' });
		}
	}
	return changes;
}

/** The whole change against the binding today (the review's combined diff). */
export function ruleChanges(edit: ReviewEdit): RuleChange[] {
	return diffRules(currentRules(edit), edit.result.rules);
}

/** One revision's own change against the one before it (revision 1: against today). */
export interface RevisionChange {
	revision: SessionRevision;
	rules: RuleChange[];
	addedScopes: string[];
	removedScopes: string[];
}

/** Each revision's own diff, oldest first. */
export function revisionChanges(
	edit: ReviewEdit,
	revisions: readonly SessionRevision[],
): RevisionChange[] {
	let previousRules: readonly EditRuleView[] = currentRules(edit);
	let previousScopes: readonly string[] = edit.current.granted_scopes ?? [];
	return revisions.map((revision) => {
		const rules = revision.result.rules ?? [];
		const scopes = revision.result.scopes ?? [];
		const change: RevisionChange = {
			revision,
			rules: diffRules(previousRules, rules).filter((c) => c.kind !== 'kept'),
			addedScopes: scopes.filter((s) => !previousScopes.includes(s)),
			removedScopes: previousScopes.filter((s) => !scopes.includes(s)),
		};
		previousRules = rules;
		previousScopes = scopes;
		return change;
	});
}

/** Scopes the result holds that the credential's grant does not. */
export function addedScopes(edit: ReviewEdit): string[] {
	const granted = new Set(edit.current.granted_scopes ?? []);
	return edit.result.scopes.filter((scope) => !granted.has(scope));
}

/** Scopes the credential's grant holds today that the result drops. */
export function removedScopes(edit: ReviewEdit): string[] {
	const result = new Set(edit.result.scopes);
	return (edit.current.granted_scopes ?? []).filter((scope) => !result.has(scope));
}

/** Whether approving switches a suspended binding back on. */
export function resumes(edit: ReviewEdit): boolean {
	return edit.current.suspended && !edit.result.suspended;
}

/** The rules to send on ``:confirm`` (the result's, without ids). */
export function rulesToConfirm(rules: readonly EditRuleView[]): PermissionRule[] {
	return rules.map(({ id: _id, added_by: _addedBy, ...rule }) => rule);
}

/** "GET, POST /v1/x (prefix)": one rule's match in words. */
export function describeRuleMatch(rule: PermissionRule): string {
	const methods = rule.methods?.length ? rule.methods.join(', ') : 'Any method';
	const path = rule.path ?? 'any path';
	const mode = rule.path ? ` (${rule.match_mode ?? 'regex'})` : '';
	const operations = rule.operations?.length ? `, operations ${rule.operations.join(', ')}` : '';
	return `${methods} ${path}${mode}${operations}`;
}

/** One conflict in words, for the review. */
export function describeConflict(conflict: EditConflict): string {
	return `A rule it names (${conflict.rule_id}) was removed since it asked.`;
}
