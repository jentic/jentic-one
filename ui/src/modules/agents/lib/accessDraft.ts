/**
 * The Manage access draft against the saved rules: whether they differ, and
 * what a save would change, in the words the panel's footer uses.
 *
 * First match wins, so ORDER is part of a rule list: a pure reorder is a
 * change. By action choices are compared per action (an id that moves from
 * Allow to Ask is one change, named), path rules per rule.
 */
import { ruleSummary } from '@/shared/lib';
import type { PermissionRuleInput } from '@/shared/ui';
import type { PermissionRule } from '@/shared/credentials/api/vendors-types';
import {
	accessMap,
	isActionRule,
	joinCustomRules,
	ruleKey,
	splitCustomRules,
	type ActionGroups,
} from '@/shared/credentials/lib/accessPresets';
import {
	actionOwner,
	describeActions,
	type ActionCatalog,
	type ActionEntry,
} from '@/shared/credentials/lib/actionRules';
import { toDisplayRule } from '@/modules/agents/components/detail/shared';

/** The whole ordered list as one key — equal iff the broker would decide alike. */
export function rulesKey(rules: readonly (PermissionRule | PermissionRuleInput)[]): string {
	return rules.map(ruleKey).join('\n');
}

type Access = keyof ActionGroups | 'unset';

export interface DraftChanges {
	/** How many changes a save makes: each re-set action, each path rule
	 * added or removed, and a reorder of the path rules as one. */
	count: number;
	/** Actions whose access changed, by what they become. */
	actions: { to: Access; names: string[] }[];
	added: string[];
	removed: string[];
	reordered: boolean;
	/** Action choices saved after path rules that this save moves ahead of
	 * them (an older order), so a choice on a specific action wins. */
	movedAhead: number;
}

/** Rules in `a` with no counterpart left in `b` (multiset semantics). */
function without(a: readonly PermissionRule[], b: readonly PermissionRule[]): PermissionRule[] {
	const left = new Map<string, number>();
	for (const r of b) left.set(ruleKey(r), (left.get(ruleKey(r)) ?? 0) + 1);
	return a.filter((r) => {
		const k = ruleKey(r);
		const n = left.get(k) ?? 0;
		if (n === 0) return true;
		left.set(k, n - 1);
		return false;
	});
}

/** One action, as short as it can be named: its summary, else method and path. */
function actionName(entry: ActionEntry): string {
	if (entry.kind !== 'found') return 'an action not in this version of the API';
	const base = entry.summary ?? `${entry.method} ${entry.path}`;
	return entry.api ? `${base} (${entry.api.title})` : base;
}

/** How many action choices sit in action rules saved after the first path rule. */
function actionsAfterPathRules(rules: readonly PermissionRule[]): number {
	const first = rules.findIndex((r) => !isActionRule(r));
	if (first === -1) return 0;
	return new Set(
		rules.slice(first + 1).flatMap((r) => (isActionRule(r) ? (r.operations ?? []) : [])),
	).size;
}

/** One path rule in the platform's rule voice, without the trailing period. */
function oneLiner(rule: PermissionRule): string {
	return ruleSummary([toDisplayRule(rule)]).replace(/\.$/, '');
}

export function draftChanges(
	saved: readonly PermissionRule[],
	draft: readonly PermissionRule[],
	catalog: ActionCatalog,
): DraftChanges {
	const a = splitCustomRules(saved);
	const b = splitCustomRules(draft);
	const before = accessMap(a.groups);
	const after = accessMap(b.groups);
	const moved = new Map<Access, string[]>();
	for (const id of new Set([...before.keys(), ...after.keys()])) {
		const was = before.get(id) ?? 'unset';
		const now = after.get(id) ?? 'unset';
		if (was === now) continue;
		moved.set(now, (moved.get(now) ?? []).concat(id));
	}
	const order: Access[] = ['allow', 'ask', 'deny', 'unset'];
	const actions = order
		.filter((to) => moved.has(to))
		.map((to) => ({
			to,
			names: describeActions(moved.get(to) ?? [], catalog).entries.map(actionName),
		}));
	const added = without(b.pathRules, a.pathRules);
	const removed = without(a.pathRules, b.pathRules);
	const reordered =
		added.length === 0 &&
		removed.length === 0 &&
		rulesKey(a.pathRules) !== rulesKey(b.pathRules);
	const actionCount = actions.reduce((n, g) => n + g.names.length, 0);
	// An older saved order (action rules after path rules) that this save puts
	// right: the action choices move ahead, and say so as a change of their own.
	const movedAhead = a.actionsMoveAhead && !b.actionsMoveAhead ? actionsAfterPathRules(saved) : 0;
	let count =
		actionCount + added.length + removed.length + (reordered ? 1 : 0) + (movedAhead ? 1 : 0);
	// Any other change of placement still counts as one, even with nothing else.
	const otherwise = count === 0 && rulesKey(saved) !== rulesKey(draft);
	if (otherwise) count = 1;
	return {
		count,
		actions,
		added: added.map(oneLiner),
		removed: removed.map(oneLiner),
		reordered: reordered || otherwise,
		movedAhead,
	};
}

/** The operation ids of the saved rules that belong to another API the rule
 * list governs — the ones this panel never lists, so never changes. */
export function siblingActionIds(
	saved: readonly PermissionRule[],
	catalog: ActionCatalog,
): Set<string> {
	const ids = saved.flatMap((r) => r.operations ?? []);
	return new Set(
		describeActions(ids, catalog)
			.entries.filter((e) => actionOwner(e, catalog) === 'other')
			.map((e) => e.id),
	);
}

/**
 * The draft as it saves: every other-API action id of the saved rules is kept
 * with the effect it was saved with, whatever the draft did around it — a
 * preset that replaced the list, a removed action rule. One list governs every
 * API the credential reaches, so a save from one API's panel must not take
 * another API's actions away. Unchanged when nothing needs keeping.
 */
export function keepSiblingActions(
	draft: readonly PermissionRule[],
	saved: readonly PermissionRule[],
	keep: ReadonlySet<string>,
): PermissionRule[] {
	if (keep.size === 0) return [...draft];
	const savedAccess = accessMap(splitCustomRules(saved).groups);
	const { pathRules, groups } = splitCustomRules(draft);
	const draftAccess = accessMap(groups);
	const lost = [...keep].filter((id) => draftAccess.get(id) !== savedAccess.get(id));
	if (lost.length === 0) return [...draft];
	const lostSet = new Set(lost);
	const next: ActionGroups = {
		allow: groups.allow.filter((id) => !lostSet.has(id)),
		ask: groups.ask.filter((id) => !lostSet.has(id)),
		deny: groups.deny.filter((id) => !lostSet.has(id)),
	};
	for (const id of lost) {
		const g = savedAccess.get(id);
		if (g) next[g].push(id);
	}
	return joinCustomRules(pathRules, next);
}
