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

/** Whether two rule lists match rule for rule, in order (first match wins). */
export function sameRules(a: readonly PermissionRule[], b: readonly PermissionRule[]): boolean {
	return a.length === b.length && a.every((rule, i) => ruleKey(rule) === ruleKey(b[i]));
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

function plural(n: number, word: string): string {
	return `${n} ${word}${n === 1 ? '' : 's'}`;
}

/** A revision's own reason: why it was asked for, as it was sent. */
export function revisionReason(revision: SessionRevision): string | null {
	const reason = revision.proposal.reason;
	return typeof reason === 'string' && reason.trim() !== '' ? reason : null;
}

/**
 * One line on what a revision did, e.g. "added 2 rules, asked to resume";
 * "replaced the rule list" for a full replacement, and the rebase onto a
 * changed binding for one no one made.
 */
export function revisionSummary(change: RevisionChange): string {
	if (change.revision.created_by === null) return 'rebased onto the access as it is now';
	const proposal = change.revision.proposal as {
		rules?: { set?: unknown };
		resume?: boolean | null;
	};
	const added = change.rules.filter((c) => c.kind === 'added').length;
	const removed = change.rules.filter((c) => c.kind === 'removed').length;
	const parts: string[] = [];
	if (proposal.rules?.set !== undefined && (added > 0 || removed > 0)) {
		parts.push('replaced the rule list');
	} else {
		if (added > 0) parts.push(`added ${plural(added, 'rule')}`);
		if (removed > 0) parts.push(`removed ${plural(removed, 'rule')}`);
	}
	if (change.addedScopes.length > 0)
		parts.push(`added ${plural(change.addedScopes.length, 'scope')}`);
	if (change.removedScopes.length > 0) {
		parts.push(`removed ${plural(change.removedScopes.length, 'scope')}`);
	}
	if (proposal.resume) parts.push('asked to resume');
	return parts.length > 0 ? parts.join(', ') : 'changed the reason only';
}

/**
 * The revision that put each added rule in the request, by rule content:
 * the latest one that added it (a rule dropped and asked for again counts
 * from its return).
 */
export function revisionsAddingRules(changes: readonly RevisionChange[]): Map<string, number> {
	const introduced = new Map<string, number>();
	for (const change of changes) {
		for (const rule of change.rules) {
			if (rule.kind === 'added') introduced.set(ruleKey(rule.rule), change.revision.number);
			else introduced.delete(ruleKey(rule.rule));
		}
	}
	return introduced;
}

/** The revision that added ``rule``, from :func:`revisionsAddingRules`. */
export function revisionAddingRule(
	introduced: ReadonlyMap<string, number>,
	rule: PermissionRule,
): number | undefined {
	return introduced.get(ruleKey(rule));
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

/** How one scope fares in the change, with who asked for it and why. */
export interface ScopeChange {
	scope: string;
	kind: 'kept' | 'added' | 'removed';
	addedBy: string | null;
	comment: string | null;
}

/** Every scope the grant holds today or the change leaves: added, removed and kept. */
export function scopeChanges(edit: ReviewEdit): ScopeChange[] {
	const granted = new Set(edit.current.granted_scopes ?? []);
	const result = new Set(edit.result.scopes);
	const notes = edit.result.scope_notes ?? {};
	return [...new Set([...granted, ...result])].sort().map((scope) => ({
		scope,
		kind: granted.has(scope) ? (result.has(scope) ? 'kept' : 'removed') : 'added',
		addedBy: notes[scope]?.added_by ?? null,
		comment: notes[scope]?.comment ?? null,
	}));
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
