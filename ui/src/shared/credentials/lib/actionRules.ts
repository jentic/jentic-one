/**
 * Action rules in human terms. An action rule names the operations it matches
 * by operation id — opaque ids the broker matches on, meaningless to a reader.
 * These helpers resolve them against the API's operation list into what each
 * action IS (method, path template, summary), and say so honestly while the
 * list is still loading, when there is no list to read, or when an id is no
 * longer in the API's current version.
 *
 * Display only: nothing here decides what a rule matches.
 */
import type { VendorOperation } from '@/shared/credentials/api/vendors-client';
import type { PermissionRule } from '@/shared/credentials/api/vendors-types';
import { classifyOpCoverage, evaluateTemplateOp } from '@/shared/credentials/lib/template-matcher';

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
	operations: readonly VendorOperation[];
}

/** One operation id of a rule, resolved as far as the catalog allows. */
export type ActionEntry =
	| {
			kind: 'found';
			id: string;
			method: string;
			path: string;
			/** The operation's summary (`name`), when the spec gives one. */
			summary: string | null;
	  }
	/** The list is complete and this id isn't in it: the API changed under the rule. */
	| { kind: 'missing'; id: string }
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

/** Resolve a rule's operation ids against the API's operation list. */
export function describeActions(
	operationIds: readonly string[],
	catalog: ActionCatalog,
): ActionsDescription {
	const byId = new Map(catalog.operations.map((op) => [op.operation_id, op]));
	const ids = [...new Set(operationIds)];
	const entries: ActionEntry[] = ids.map((id) => {
		const op = byId.get(id);
		if (op) {
			return {
				kind: 'found',
				id,
				method: op.method.toUpperCase(),
				path: op.path,
				summary: op.name?.trim() || null,
			};
		}
		return catalog.status === 'ready' ? { kind: 'missing', id } : { kind: 'unresolved', id };
	});
	entries.sort((a, b) => {
		const byKind = ENTRY_ORDER[a.kind] - ENTRY_ORDER[b.kind];
		if (byKind !== 0) return byKind;
		if (a.kind === 'found' && b.kind === 'found') {
			return a.path.localeCompare(b.path) || a.method.localeCompare(b.method);
		}
		return 0;
	});
	const tally = (kind: ActionEntry['kind']): number =>
		entries.filter((e) => e.kind === kind).length;
	return {
		status: catalog.status,
		count: ids.length,
		entries,
		found: tally('found'),
		missing: tally('missing'),
		unresolved: tally('unresolved'),
	};
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
		case 'found':
			return entry.summary
				? `${entry.method} ${entry.path} · ${entry.summary}`
				: `${entry.method} ${entry.path}`;
		case 'missing':
			return 'An action not in this version of the API';
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
 * How many of these actions an earlier path rule already decides — for the
 * By action list's warnings:
 *
 * - `'allow'`: a path rule allows the call (wholly, or for some of its paths),
 *   so an Ask action runs without asking.
 * - `'deny'`: a path deny can match the call first, so an Allow or Ask action
 *   is blocked there.
 *
 * Only actions the catalog can name are judged; the rest can't be.
 */
export function actionsDecidedByPathRules(
	ids: readonly string[],
	pathRules: readonly PermissionRule[],
	catalog: ActionCatalog,
	decidedAs: 'allow' | 'deny',
): number {
	if (ids.length === 0 || pathRules.length === 0) return 0;
	const byId = new Map(catalog.operations.map((op) => [op.operation_id, op]));
	let count = 0;
	for (const id of new Set(ids)) {
		const op = byId.get(id);
		if (!op) continue;
		const target = { method: op.method, path: op.path, operation_id: op.operation_id };
		if (decidedAs === 'deny') {
			if (evaluateTemplateOp(pathRules, target).matchingRule?.effect === 'deny') count += 1;
			continue;
		}
		const coverage = classifyOpCoverage(pathRules, target);
		if (
			coverage.verdict === 'allow' ||
			(coverage.verdict === 'partial' && coverage.allowedSamples.length > 0)
		) {
			count += 1;
		}
	}
	return count;
}
