/**
 * Action rules in human terms. An action rule names the operations it matches
 * by id — opaque to a reader, and in one of two forms the broker matches on:
 * the registry id (`op_…`), new with every revision of the API, or the spec's
 * `operationId` (`createOrder`), which survives a re-import. These helpers
 * resolve both against the API's operation list into what each action IS
 * (method, path template, summary), and say so honestly while the list is
 * still loading, when there is no list to read, or when an id is no longer in
 * the API's current version — naming what it was, when an earlier version
 * tells.
 *
 * Display and choice only: nothing here decides what a rule matches.
 */
import type { VendorOperation } from '@/shared/credentials/api/vendors-client';
import { actionAt } from '@/shared/credentials/lib/specOperationIds';

/**
 * An operation of the catalog, with what its spec says about it. The `spec_*`
 * and `action_key` fields are set once the API's current document is read.
 */
export interface ActionOperation extends VendorOperation {
	/** The stored spec `operationId`; null when it has none stored whole. */
	spec_operation_id?: string | null;
	/** The id a rule set here names it by: its `operationId` when that is
	 * unique across what the credential covers, else its registry id. */
	action_key?: string;
	/** Another API the credential covers declares the same `operationId`
	 * (their names, joined). */
	ambiguous_with?: string | null;
	/** Every id a rule set here writes for it, when that is more than its
	 * `action_key`: an ambiguous action, named in each API that declares it. */
	action_keys?: readonly string[];
}

/** What an id of an earlier revision named. */
export interface PastActionRef {
	method: string;
	path: string;
	summary: string | null;
}

/** `op_` and 40 hex characters: the registry's id, new in every revision. */
export function isRegistryOperationId(id: string): boolean {
	return /^op_[0-9a-f]{40}$/.test(id);
}

/** The id a rule set here names `op` by. */
export function actionKeyOf(op: ActionOperation): string {
	return op.action_key ?? op.operation_id;
}

/** Every id a rule set here writes for `op` (see {@link ActionOperation.action_keys}). */
export function actionKeysOf(op: ActionOperation): readonly string[] {
	return op.action_keys ?? [actionKeyOf(op)];
}

/**
 * The ids a rule of `effect` writes for `op`. A Deny on an ambiguous action
 * keeps its shared `operationId`: the broker still denies an ambiguous name,
 * and the name survives re-imports where each API's registry id does not. An
 * Ask or an Allow can't — the broker would refuse it or grant nothing — so it
 * names the action's registry id in each API that declares it.
 */
export function actionKeysFor(
	op: ActionOperation,
	effect: 'allow' | 'ask' | 'deny',
): readonly string[] {
	if (effect === 'deny' && op.ambiguous_with && op.spec_operation_id) {
		return [op.spec_operation_id];
	}
	return actionKeysOf(op);
}

/**
 * The ids that name one of this API's ambiguous actions in another covered
 * API — the same action, written beside this API's id so a rule holds
 * whichever API a call resolves to. A save or a removal from this panel moves
 * them with the action, rather than keeping them as another API's.
 */
export function twinActionIds(catalog: ActionCatalog): Set<string> {
	const twins = new Set<string>();
	for (const op of catalog.operations) {
		for (const id of op.action_keys ?? []) if (id !== op.operation_id) twins.add(id);
	}
	return twins;
}

/**
 * The API's operation list as far as it is known.
 *
 * - `loading` — still being read; `operations` holds the pages read so far.
 * - `ready` — the whole list.
 * - `unavailable` — there is no list to read (no API reference, no current
 *   revision, or the read failed); `operations` holds whatever did load.
 */
export interface ActionCatalog {
	status: 'loading' | 'ready' | 'unavailable';
	operations: readonly ActionOperation[];
	/**
	 * Whether the stored `operationId`s of the API's operations are known
	 * (read, or there are none to read). Until then an id in that form can't
	 * be proven gone. Absent reads as known.
	 */
	specIdsKnown?: boolean;
	/** What ids of the API's earlier revisions named, when they were read. */
	past?: ReadonlyMap<string, PastActionRef>;
	/**
	 * False while the ids a rule set here would write are still being worked
	 * out — the spec's `operationId`s, or which other covered APIs share them.
	 * A choice made then could name an action in one API only. Absent reads as
	 * settled.
	 */
	keysSettled?: boolean;
	/**
	 * The other APIs the same rule list governs. Rules belong to the binding
	 * (agent × credential), and a credential that reaches several APIs shares
	 * one list across them, so an id absent here may name an action there.
	 */
	elsewhere?: readonly SiblingActionCatalog[];
}

/** Another API's operation list, for naming ids that belong to it. */
export interface SiblingActionCatalog {
	/** Stable key for the API (the host's tile key). */
	key: string;
	/** How the API reads to a person ("Archive"). */
	title: string;
	status: ActionCatalog['status'];
	operations: readonly ActionOperation[];
	/** As {@link ActionCatalog.specIdsKnown}. */
	specIdsKnown?: boolean;
}

/** One operation id of a rule, resolved as far as the catalog allows. */
export type ActionEntry =
	| {
			kind: 'found';
			id: string;
			method: string;
			path: string;
			/** What it does in plain English (see {@link operationText}). */
			summary: string | null;
			/** `summary` was described from the method and path, not by the API. */
			generated?: boolean;
			/** The other API the action belongs to; absent for this API's own. */
			api?: { key: string; title: string };
			/** The id is an `operationId` another API the credential covers
			 * declares too: the broker can't tell which one the rule means. */
			ambiguousWith?: string;
	  }
	/**
	 * The list is complete and this id isn't in it: the API changed under the
	 * rule. `was` names the action when an earlier version has it, and `now` is
	 * the current operation at the same method and path, when there is one.
	 */
	| {
			kind: 'missing';
			id: string;
			was?: PastActionRef;
			now?: { key: string; keys: readonly string[]; method: string; path: string };
	  }
	/** Not resolved yet (still loading) or not resolvable (no list). */
	| { kind: 'unresolved'; id: string };

export interface ActionsDescription {
	status: ActionCatalog['status'];
	/** Distinct operation ids on the rule. */
	count: number;
	/** Found actions first (by path, then method), then missing, then unresolved. */
	entries: ActionEntry[];
	found: number;
	missing: number;
	unresolved: number;
}

const ENTRY_ORDER: Record<ActionEntry['kind'], number> = { found: 0, missing: 1, unresolved: 2 };

/** Every list the catalog can read is complete — only then is an id provably gone. */
export function catalogComplete(catalog: ActionCatalog): boolean {
	return (
		catalog.status === 'ready' && (catalog.elsewhere ?? []).every((s) => s.status === 'ready')
	);
}

/** Every operation the catalog knows: this API's, then each other API's. */
export function catalogOperations(catalog: ActionCatalog): readonly ActionOperation[] {
	return [...catalog.operations, ...(catalog.elsewhere ?? []).flatMap((s) => s.operations)];
}

/** Every id an operation answers to: its registry id, and its `operationId`. */
function idsOf(op: ActionOperation): string[] {
	return op.spec_operation_id ? [op.operation_id, op.spec_operation_id] : [op.operation_id];
}

/** An operation list indexed by every id its operations answer to. */
function indexById(ops: readonly ActionOperation[]): Map<string, ActionOperation> {
	const byId = new Map<string, ActionOperation>();
	for (const op of ops) for (const id of idsOf(op)) byId.set(id, op);
	return byId;
}

/**
 * Every id that names each of this API's operations, by its registry id: the
 * registry id, its `operationId`, and any id of an earlier revision that named
 * the same method and path. Setting an action replaces all of them, so a rule
 * never holds the same action twice — and a re-set retires the stale id.
 */
export function actionAliases(catalog: ActionCatalog): Map<string, ReadonlySet<string>> {
	const pastAt = new Map<string, string[]>();
	for (const [id, was] of catalog.past ?? []) {
		const at = actionAt(was.method, was.path);
		pastAt.set(at, [...(pastAt.get(at) ?? []), id]);
	}
	return new Map(
		catalog.operations.map((op) => [
			op.operation_id,
			new Set([
				...idsOf(op),
				...(op.action_keys ?? []),
				...(pastAt.get(actionAt(op.method, op.path)) ?? []),
			]),
		]),
	);
}

/**
 * Resolve a rule's operation ids against the API's operation list, then
 * against the other APIs the rule list governs. An id is `missing` only when
 * every list is complete and none has it.
 */
export function describeActions(
	operationIds: readonly string[],
	catalog: ActionCatalog,
): ActionsDescription {
	const byId = indexById(catalog.operations);
	const elsewhere = catalog.elsewhere ?? [];
	const siblingById = elsewhere.map((s) => ({ s, byId: indexById(s.operations) }));
	const complete = catalogComplete(catalog);
	// An `operationId` is provably gone only once every list's ids are known.
	const specIdsKnown =
		catalog.specIdsKnown !== false && elsewhere.every((s) => s.specIdsKnown !== false);
	const nowAt = new Map(catalog.operations.map((op) => [actionAt(op.method, op.path), op]));
	const ids = [...new Set(operationIds)];
	const entries: ActionEntry[] = ids.map((id) => {
		const own = byId.get(id);
		const sib = own ? undefined : siblingById.find(({ byId: m }) => m.has(id));
		const op = own ?? sib?.byId.get(id);
		if (op) {
			const plain = operationText(op);
			const ambiguous = id === op.spec_operation_id ? op.ambiguous_with : null;
			return {
				kind: 'found',
				id,
				method: op.method.toUpperCase(),
				path: op.path,
				summary: plain.text,
				...(plain.generated && { generated: true }),
				...(sib && { api: { key: sib.s.key, title: sib.s.title } }),
				...(ambiguous && { ambiguousWith: ambiguous }),
			};
		}
		if (!complete || (!isRegistryOperationId(id) && !specIdsKnown)) {
			return { kind: 'unresolved', id };
		}
		const was = catalog.past?.get(id);
		const current = was ? nowAt.get(actionAt(was.method, was.path)) : undefined;
		return {
			kind: 'missing',
			id,
			...(was && { was }),
			...(current && {
				now: {
					key: actionKeyOf(current),
					keys: actionKeysOf(current),
					method: current.method,
					path: current.path,
				},
			}),
		};
	});
	entries.sort((a, b) => {
		const byKind = ENTRY_ORDER[a.kind] - ENTRY_ORDER[b.kind];
		if (byKind !== 0) return byKind;
		if (a.kind === 'found' && b.kind === 'found') {
			// This API's own actions first, then each other API's.
			const byApi =
				(a.api ? 1 : 0) - (b.api ? 1 : 0) ||
				(a.api?.title ?? '').localeCompare(b.api?.title ?? '');
			if (byApi !== 0) return byApi;
			return a.path.localeCompare(b.path) || a.method.localeCompare(b.method);
		}
		return 0;
	});
	const tally = (kind: ActionEntry['kind']): number =>
		entries.filter((e) => e.kind === kind).length;
	return {
		status: complete
			? 'ready'
			: [catalog, ...elsewhere].some((c) => c.status === 'loading')
				? 'loading'
				: 'unavailable',
		count: ids.length,
		entries,
		found: tally('found'),
		missing: tally('missing'),
		unresolved: tally('unresolved'),
	};
}

/**
 * Whose a resolved id is, from this API's panel: its own, another API's that
 * the same list governs, or gone from every list. Once this API's own list is
 * whole, an id it lacks that no other list names yet (one still loading, or
 * unreadable) can only be another API's.
 */
export function actionOwner(entry: ActionEntry, catalog: ActionCatalog): 'own' | 'other' | 'gone' {
	if (entry.kind === 'found') return entry.api ? 'other' : 'own';
	if (entry.kind === 'unresolved') return catalog.status === 'ready' ? 'other' : 'own';
	return 'gone';
}

/** "1 action" / "5 actions". */
export function actionCount(count: number): string {
	return `${count.toLocaleString()} ${count === 1 ? 'action' : 'actions'}`;
}

/**
 * The rule's actions in brief, for compact spots: the action itself
 * (`GET /v1/boardColumns`) when there is exactly one and it is known,
 * otherwise the count (`5 actions`).
 */
export function actionsSummary(description: ActionsDescription): string {
	const only = description.count === 1 ? description.entries[0] : undefined;
	if (only?.kind === 'found') return `${only.method} ${only.path}`;
	return actionCount(description.count);
}

/** One action as a line of text: `GET /v1/boardColumns · List board columns`. */
export function actionLine(entry: ActionEntry): string {
	switch (entry.kind) {
		case 'found': {
			const line = entry.summary
				? `${entry.method} ${entry.path} · ${entry.summary}`
				: `${entry.method} ${entry.path}`;
			return entry.api ? `${line} — on ${entry.api.title}` : line;
		}
		case 'missing':
			return entry.was
				? `${entry.was.method} ${entry.was.path}${entry.was.summary ? ` · ${entry.was.summary}` : ''} — from an earlier version`
				: 'An action not in this version of the API';
		default:
			return 'An action not resolved yet';
	}
}

/** Why some actions can't be named, or null when every one is. */
export function actionsNote(description: ActionsDescription): string | null {
	if (description.unresolved > 0) {
		return description.status === 'loading'
			? `Reading this API's actions…`
			: `This API's list of actions isn't available, so ${
					description.unresolved === description.count ? 'they' : 'some'
				} can't be named.`;
	}
	if (description.missing > 0) {
		return description.missing === 1
			? 'One action is not in this version of the API.'
			: `${description.missing.toLocaleString()} actions are not in this version of the API.`;
	}
	return null;
}

/**
 * Readable lines for a tooltip: the known actions (up to `limit`), then
 * "+N more" for the rest, then a note when some can't be named. Ids never
 * appear — they are secondary detail, not something to read.
 */
export function actionTooltipLines(description: ActionsDescription, limit = 5): string[] {
	const found = description.entries.filter((e) => e.kind === 'found');
	const shown = found.slice(0, limit).map(actionLine);
	const rest = description.found - shown.length;
	const lines = [...shown];
	if (rest > 0) lines.push(`+${rest.toLocaleString()} more`);
	const note = actionsNote(description);
	if (note) lines.push(note);
	return lines;
}

/**
 * The chosen actions that belong to OTHER APIs the rule list governs, per API
 * and effect — what the By action list can't show (it lists this API's
 * actions only) but must keep exactly as they are on save.
 */
export interface SiblingActions {
	key: string;
	title: string;
	allow: number;
	ask: number;
	deny: number;
}

export function siblingActions(
	groups: { allow: readonly string[]; ask: readonly string[]; deny: readonly string[] },
	catalog: ActionCatalog,
): SiblingActions[] {
	const own = new Set([...catalog.operations.flatMap(idsOf), ...twinActionIds(catalog)]);
	const out: SiblingActions[] = [];
	for (const s of catalog.elsewhere ?? []) {
		const theirs = new Set(s.operations.flatMap(idsOf));
		const count = (ids: readonly string[]): number =>
			new Set(ids.filter((id) => !own.has(id) && theirs.has(id))).size;
		const row = {
			key: s.key,
			title: s.title,
			allow: count(groups.allow),
			ask: count(groups.ask),
			deny: count(groups.deny),
		};
		if (row.allow + row.ask + row.deny > 0) out.push(row);
	}
	return out;
}

type Groups = { allow: readonly string[]; ask: readonly string[]; deny: readonly string[] };
const GROUP_KEYS = ['deny', 'ask', 'allow'] as const;

/** Each id this API's current operations answer to, mapped to the operation's registry id. */
function currentIds(catalog: ActionCatalog): Map<string, string> {
	const out = new Map<string, string>();
	for (const op of catalog.operations) for (const id of idsOf(op)) out.set(id, op.operation_id);
	return out;
}

/**
 * The groups as the By action list reads them: every id this API's current
 * operations answer to becomes the operation's registry id (the list's row
 * key). Other ids — another API's, or a stale one — stay as they are, so they
 * never show as a row's choice.
 */
export function viewActionGroups(
	groups: Groups,
	catalog: ActionCatalog,
): { allow: string[]; ask: string[]; deny: string[] } {
	const canon = currentIds(catalog);
	const out = { allow: [] as string[], ask: [] as string[], deny: [] as string[] };
	const seen = new Set<string>();
	for (const g of GROUP_KEYS) {
		for (const id of groups[g]) {
			const key = canon.get(id) ?? id;
			if (seen.has(key)) continue;
			seen.add(key);
			out[g].push(key);
		}
	}
	return { allow: out.allow, ask: out.ask, deny: out.deny };
}

/**
 * The stored groups after the By action list changed its view of them
 * (`before` → `after`, both from {@link viewActionGroups}). An action whose
 * choice changed is written by the id a rule set here uses
 * ({@link actionKeyOf}), and every other id that named it — its other current
 * id, and any id of an earlier revision for the same method and path — is
 * retired. Every action left as it was keeps exactly the ids it was saved with.
 */
export function storeActionChoices(
	stored: Groups,
	before: Groups,
	after: Groups,
	catalog: ActionCatalog,
): { allow: string[]; ask: string[]; deny: string[] } {
	const was = accessOf(before);
	const now = accessOf(after);
	const changed = new Set<string>();
	for (const id of new Set([...was.keys(), ...now.keys()])) {
		if (was.get(id) !== now.get(id)) changed.add(id);
	}
	const aliases = actionAliases(catalog);
	const opById = new Map(catalog.operations.map((op) => [op.operation_id, op]));
	const retire = new Set<string>();
	for (const id of changed) {
		retire.add(id);
		for (const alias of aliases.get(id) ?? []) retire.add(alias);
	}
	const out = {
		allow: stored.allow.filter((id) => !retire.has(id)),
		ask: stored.ask.filter((id) => !retire.has(id)),
		deny: stored.deny.filter((id) => !retire.has(id)),
	};
	for (const id of changed) {
		const g = now.get(id);
		if (!g) continue;
		const op = opById.get(id);
		out[g].push(...(op ? actionKeysFor(op, g) : [id]));
	}
	return out;
}

/** Each id's group, as {@link accessMap} reads it (Deny, then Ask, then Allow). */
function accessOf(groups: Groups): Map<string, (typeof GROUP_KEYS)[number]> {
	const map = new Map<string, (typeof GROUP_KEYS)[number]>();
	for (const g of GROUP_KEYS) for (const id of groups[g]) if (!map.has(id)) map.set(id, g);
	return map;
}

/**
 * How many actions two stored sets of choices treat differently — by action,
 * not by id: re-setting a stale Deny to Deny is one change (the id it is held
 * by changes), not two.
 */
export function changedActionChoices(a: Groups, b: Groups, catalog: ActionCatalog): number {
	const canon = new Map<string, string>();
	for (const [reg, ids] of actionAliases(catalog)) for (const id of ids) canon.set(id, reg);
	const state = (groups: Groups): Map<string, string> => {
		const byAction = new Map<string, string[]>();
		for (const g of GROUP_KEYS) {
			for (const id of groups[g]) {
				const key = canon.get(id) ?? id;
				byAction.set(key, [...(byAction.get(key) ?? []), `${g}:${id}`]);
			}
		}
		return new Map([...byAction].map(([k, v]) => [k, v.sort().join(' ')]));
	};
	const x = state(a);
	const y = state(b);
	let n = 0;
	for (const key of new Set([...x.keys(), ...y.keys()])) if (x.get(key) !== y.get(key)) n += 1;
	return n;
}

/**
 * The action rules brought up to the API's current version: every id this
 * API's current operations answer to is rewritten to the id a rule set here
 * would use (its `operationId` where unique), and every stale id whose action
 * the current version still has, to that action. The effect stays the one the
 * id was saved with. `left` counts the stale ids that name nothing current
 * (removed or renamed), which only a person can resolve.
 */
export function actionsAtThisVersion<
	G extends { allow: readonly string[]; ask: readonly string[]; deny: readonly string[] },
>(
	groups: G,
	catalog: ActionCatalog,
): { groups: { allow: string[]; ask: string[]; deny: string[] }; changed: number; left: number } {
	const byId = indexById(catalog.operations);
	const nowAt = new Map(catalog.operations.map((op) => [actionAt(op.method, op.path), op]));
	// A stale id, to the current operation at the same method and path (null: none).
	const stale = new Map<string, ActionOperation | null>();
	for (const e of describeActions([...groups.allow, ...groups.ask, ...groups.deny], catalog)
		.entries) {
		if (e.kind === 'missing') {
			stale.set(e.id, e.now ? (nowAt.get(actionAt(e.now.method, e.now.path)) ?? null) : null);
		}
	}
	// Another API's copy of one of this API's ambiguous actions, to that action.
	const twinOf = new Map<string, ActionOperation>();
	for (const op of catalog.operations) {
		for (const id of op.action_keys ?? []) if (id !== op.operation_id) twinOf.set(id, op);
	}
	let changed = 0;
	let left = 0;
	const seen = new Set<string>();
	const move = (ids: readonly string[], effect: 'allow' | 'ask' | 'deny'): string[] => {
		const out: string[] = [];
		const held = new Set(ids);
		for (const id of ids) {
			const op = byId.get(id) ?? twinOf.get(id);
			let to: readonly string[] = [id];
			if (op) to = actionKeysFor(op, effect);
			else if (stale.has(id)) {
				const now = stale.get(id);
				if (now) to = actionKeysFor(now, effect);
				else left += 1;
			}
			// Rewritten: it names the action some other way, or misses a copy.
			if (to.some((key) => !held.has(key))) changed += 1;
			for (const key of to) {
				if (seen.has(key)) continue;
				seen.add(key);
				out.push(key);
			}
		}
		return out;
	};
	// Deny first: an id that lands in two effects keeps the one the By action
	// list reads it as (`accessMap`: Deny, then Ask, then Allow).
	const deny = move(groups.deny, 'deny');
	const ask = move(groups.ask, 'ask');
	const allow = move(groups.allow, 'allow');
	return { groups: { allow, ask, deny }, changed, left };
}

type FoundEntry = Extract<ActionEntry, { kind: 'found' }>;

/**
 * What is wrong with one action rule's ambiguous actions — the ones whose
 * `operationId` another API the credential covers declares too.
 *
 * - `shared`: an Ask or Allow names the shared `operationId`. The broker
 *   can't tell which API it means, so the Ask refuses the call and the Allow
 *   grants nothing.
 * - `partial`: it names the action's registry id in some of those APIs only;
 *   a call the broker resolves to another one skips the rule.
 * - `byCode`: a Deny names the action by registry ids. It holds today, but
 *   the ids change with a re-import, where the shared name (which a Deny can
 *   use) would not.
 */
export interface RuleAmbiguity {
	shared: FoundEntry[];
	partial: FoundEntry[];
	byCode: FoundEntry[];
	/** Each flagged id to what a rule of this effect should hold instead
	 * (`actionKeysFor`); an id the fix makes redundant maps to nothing. */
	renames: Map<string, readonly string[]>;
}

export function ruleAmbiguity(
	ids: readonly string[],
	effect: 'allow' | 'ask' | 'deny',
	catalog: ActionCatalog,
): RuleAmbiguity {
	const own = new Map<string, ActionOperation>();
	for (const op of catalog.operations) {
		own.set(op.operation_id, op);
		if (op.spec_operation_id) own.set(op.spec_operation_id, op);
	}
	const twinOf = new Map<string, ActionOperation>();
	for (const op of catalog.operations) {
		for (const id of op.action_keys ?? []) if (id !== op.operation_id) twinOf.set(id, op);
	}
	const held = new Set(ids);
	const out: RuleAmbiguity = { shared: [], partial: [], byCode: [], renames: new Map() };
	const flagged = new Set<ActionOperation>();
	for (const e of describeActions(ids, catalog).entries) {
		if (e.kind !== 'found' || e.api) continue;
		const op = own.get(e.id);
		if (!op?.ambiguous_with) continue;
		if (e.id === op.spec_operation_id) {
			if (effect === 'deny') continue;
			out.shared.push(e);
		} else if (effect === 'deny') {
			out.byCode.push(e);
		} else if ((op.action_keys ?? []).some((key) => !held.has(key))) {
			out.partial.push(e);
		} else continue;
		flagged.add(op);
	}
	for (const op of flagged) {
		const mine = ids.filter((id) => own.get(id) === op || twinOf.get(id) === op);
		mine.forEach((id, i) => out.renames.set(id, i === 0 ? actionKeysFor(op, effect) : []));
	}
	return out;
}

/** "Archive, Top Stories and Search". */
export function joinAnd(parts: readonly string[]): string {
	if (parts.length <= 1) return parts.join('');
	return `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}`;
}

/** "Also allows 1 action and asks before 2 on Archive". */
export function siblingActionsLine(row: SiblingActions): string {
	const parts = [
		row.allow > 0 && `allows ${actionCount(row.allow)}`,
		row.ask > 0 && `asks before ${actionCount(row.ask)}`,
		row.deny > 0 && `denies ${actionCount(row.deny)}`,
	].filter(Boolean) as string[];
	return `Also ${joinAnd(parts)} on ${row.title}`;
}

/** Acronyms kept uppercase when a path segment is humanised. */
const ACRONYMS = new Set([
	'api',
	'b2b',
	'csv',
	'dns',
	'fcm',
	'hlr',
	'http',
	'id',
	'ids',
	'ip',
	'json',
	'mfa',
	'otp',
	'pdf',
	'seo',
	'sms',
	'ssl',
	'url',
	'uuid',
]);

/** Letters whose spoken name starts with a vowel sound ("an FCM", "an HLR"). */
const VOWEL_LETTER_NAMES = new Set(['a', 'e', 'f', 'h', 'i', 'l', 'm', 'n', 'o', 'r', 's', 'x']);

/** A path segment as words: camelCase, kebab-, snake_ and dot.case split. */
function words(segment: string): string[] {
	return segment
		.replace(/([a-z0-9])([A-Z])/g, '$1 $2')
		.split(/[\s\-_.]+/)
		.filter(Boolean)
		.map((w) => w.toLowerCase());
}

function singular(word: string): string {
	if (ACRONYMS.has(word)) return word === 'ids' ? 'id' : word;
	if (/ies$/.test(word)) return word.replace(/ies$/, 'y');
	if (/(ss|us|is)$/.test(word)) return word;
	if (/(xes|ches|shes|sses)$/.test(word)) return word.replace(/es$/, '');
	return word.replace(/s$/, '');
}

function phrase(ws: readonly string[]): string {
	return ws.map((w) => (ACRONYMS.has(w) ? w.toUpperCase() : w)).join(' ');
}

/** Words spelt with a leading vowel but spoken with a consonant ("a user", "a unit"). */
const SPOKEN_CONSONANT = /^u(?:ni|sa|se|ti)/;

function article(ws: readonly string[]): string {
	const first = ws[0] ?? '';
	if (ACRONYMS.has(first)) return VOWEL_LETTER_NAMES.has(first[0]) ? 'an' : 'a';
	if (SPOKEN_CONSONANT.test(first)) return 'a';
	return /^[aeiou]/.test(first) ? 'an' : 'a';
}

/** Path segments that are themselves the operation's verb. */
const PATH_VERBS = new Set([
	'activate',
	'approve',
	'cancel',
	'complete',
	'deactivate',
	'export',
	'import',
	'publish',
	'read',
	'refresh',
	'register',
	'resubmit',
	'retry',
	'search',
	'send',
	'sync',
	'validate',
	'verify',
]);

/** Path verbs that name a state a write puts the resource in, not an action on it:
 * `PUT /notifications/read` marks notifications read; it doesn't read one. */
const STATE_VERBS = new Set(['read', 'unread', 'complete', 'seen']);

const isParam = (segment: string): boolean => /^\{.*\}$/.test(segment) || segment.startsWith(':');
/** Version and mount segments that name no resource. */
const isNoise = (segment: string): boolean => /^v\d+(\.\d+)*$/i.test(segment) || segment === 'api';

/**
 * A plain-English phrase for an operation the spec doesn't describe, built
 * only from its method and path: GET a collection → "List board columns", GET
 * one item → "Get a board column", POST → "Create a board column", PUT/PATCH →
 * "Update a custom event", DELETE → "Delete an FCM device token". The last
 * resource segment names it (singular after a `{param}`, or for one-item
 * verbs); a trailing `…Id` segment reads "… by external ID". A resource that
 * belongs to an earlier `{param}`'s owner is named with it — `/users/{id}/
 * devices/fcm/{token}` is "a user's FCM" — so two paths that end alike read
 * apart. A write to a state verb (`PUT /notifications/read`) marks the
 * resource as that state rather than performing it.
 */
export function describeFromMethodAndPath(method: string, path: string): string {
	const m = method.toUpperCase();
	const segs = path.split('/').filter((s) => s && !isNoise(s));
	const last = segs.length - 1;
	let i = last;
	while (i >= 0 && isParam(segs[i])) i -= 1;
	if (i < 0) return `${m.charAt(0)}${m.slice(1).toLowerCase()} the root resource`;
	const endsWithParam = i < last;
	let noun = words(segs[i]);
	let suffix = '';
	// `/customEvents/externalId/{externalId}` is a custom event, found by its external id.
	const prev = segs
		.slice(0, i)
		.filter((s) => !isParam(s))
		.pop();
	// `/users/{userId}/devices/fcm/{token}`: a `{param}` further up (not the one
	// right before the resource, which names the resource itself below) means
	// the resource is one owner's — say whose.
	let owner: string[] = [];
	for (let j = i - 2; j >= 1; j -= 1) {
		if (isParam(segs[j]) && !isParam(segs[j - 1])) {
			owner = words(segs[j - 1]).map(singular);
			break;
		}
	}
	const owned = (text: string): string =>
		owner.length > 0 ? `${article(owner)} ${phrase(owner)}'s ${text}` : text;
	// `/promotions/{id}/activate`, `/instagram/verifyAccount`: the path names
	// the verb itself.
	const head = noun[0] ?? '';
	if (PATH_VERBS.has(head)) {
		const rest = noun.slice(1);
		const ownerNoun = prev ? words(prev).map(singular) : [];
		const object = rest.length > 0 ? rest : ownerNoun;
		if (STATE_VERBS.has(head) && rest.length === 0 && prev && m !== 'GET' && m !== 'DELETE') {
			return `Mark ${phrase(words(prev))} as ${head}`;
		}
		const lead = `${head.charAt(0).toUpperCase()}${head.slice(1)}`;
		return object.length > 0 ? `${lead} ${article(object)} ${phrase(object)}` : lead;
	}
	if (prev && /id$/i.test(segs[i]) && endsWithParam) {
		suffix = ` by ${phrase(noun)}`;
		noun = words(prev);
	} else if (prev && !endsWithParam && !/s$/.test(noun[noun.length - 1] ?? '') && m === 'GET') {
		// `/notifications/count` reads as the notifications' count.
		noun = [...words(prev).map(singular), ...noun];
	}
	// `/listings/{id}/images`: a part of one listing, named with it.
	if (prev && i > 0 && isParam(segs[i - 1])) noun = [...words(prev).map(singular), ...noun];
	const verb =
		m === 'GET'
			? endsWithParam || !/s$/.test(noun[noun.length - 1] ?? '')
				? 'Get'
				: 'List'
			: m === 'POST'
				? 'Create'
				: m === 'PUT' || m === 'PATCH'
					? 'Update'
					: m === 'DELETE'
						? 'Delete'
						: `${m.charAt(0)}${m.slice(1).toLowerCase()}`;
	if (verb === 'List') return `List ${owned(phrase(noun))}${suffix}`;
	const one = [...noun.slice(0, -1), singular(noun[noun.length - 1] ?? '')];
	return owner.length > 0
		? `${verb} ${owned(phrase(one))}${suffix}`
		: `${verb} ${article(one)} ${phrase(one)}${suffix}`;
}

/** The first sentence of a description, trimmed to one readable line. */
function firstSentence(text: string): string | null {
	const flat = text.replace(/\s+/g, ' ').trim();
	if (!flat) return null;
	const end = flat.search(/[.!?](\s|$)/);
	const sentence = (end === -1 ? flat : flat.slice(0, end)).trim();
	return sentence.length > 0 && sentence.length <= 140 ? sentence : null;
}

/**
 * What an operation does, in plain English: the spec's summary, else the
 * first sentence of its description, else a phrase described from its method
 * and path (`generated`, so hosts can style it apart from the API's own words).
 */
export function operationText(op: {
	method: string;
	path: string;
	name?: string | null;
	description?: string | null;
}): { text: string; generated: boolean } {
	const summary = op.name?.trim();
	if (summary) return { text: summary, generated: false };
	const described = op.description ? firstSentence(op.description) : null;
	if (described) return { text: described, generated: false };
	return { text: describeFromMethodAndPath(op.method, op.path), generated: true };
}
