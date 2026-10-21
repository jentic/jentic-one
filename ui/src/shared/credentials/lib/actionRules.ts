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
	operations: readonly VendorOperation[];
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

/** Every list the catalog can read is complete — only then is an id provably gone. */
export function catalogComplete(catalog: ActionCatalog): boolean {
	return (
		catalog.status === 'ready' && (catalog.elsewhere ?? []).every((s) => s.status === 'ready')
	);
}

/** Every operation the catalog knows: this API's, then each other API's. */
export function catalogOperations(catalog: ActionCatalog): readonly VendorOperation[] {
	return [...catalog.operations, ...(catalog.elsewhere ?? []).flatMap((s) => s.operations)];
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
	const byId = new Map(catalog.operations.map((op) => [op.operation_id, op]));
	const elsewhere = catalog.elsewhere ?? [];
	const siblingById = elsewhere.map((s) => ({
		s,
		byId: new Map(s.operations.map((op) => [op.operation_id, op])),
	}));
	const complete = catalogComplete(catalog);
	const ids = [...new Set(operationIds)];
	const entries: ActionEntry[] = ids.map((id) => {
		const own = byId.get(id);
		const sib = own ? undefined : siblingById.find(({ byId: m }) => m.has(id));
		const op = own ?? sib?.byId.get(id);
		if (op) {
			const plain = operationText(op);
			return {
				kind: 'found',
				id,
				method: op.method.toUpperCase(),
				path: op.path,
				summary: plain.text,
				...(plain.generated && { generated: true }),
				...(sib && { api: { key: sib.s.key, title: sib.s.title } }),
			};
		}
		return complete ? { kind: 'missing', id } : { kind: 'unresolved', id };
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
	const own = new Set(catalog.operations.map((op) => op.operation_id));
	const out: SiblingActions[] = [];
	for (const s of catalog.elsewhere ?? []) {
		const theirs = new Set(s.operations.map((op) => op.operation_id));
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

function article(ws: readonly string[]): string {
	const first = ws[0] ?? '';
	if (ACRONYMS.has(first)) return VOWEL_LETTER_NAMES.has(first[0]) ? 'an' : 'a';
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

const isParam = (segment: string): boolean => /^\{.*\}$/.test(segment) || segment.startsWith(':');
/** Version and mount segments that name no resource. */
const isNoise = (segment: string): boolean => /^v\d+(\.\d+)*$/i.test(segment) || segment === 'api';

/**
 * A plain-English phrase for an operation the spec doesn't describe, built
 * only from its method and path: GET a collection → "List board columns", GET
 * one item → "Get a board column", POST → "Create a board column", PUT/PATCH →
 * "Update a custom event", DELETE → "Delete an FCM device token". The last
 * resource segment names it (singular after a `{param}`, or for one-item
 * verbs); a trailing `…Id` segment reads "… by external ID".
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
	// `/promotions/{id}/activate`, `/instagram/verifyAccount`: the path names
	// the verb itself.
	const head = noun[0] ?? '';
	if (PATH_VERBS.has(head)) {
		const rest = noun.slice(1);
		const owner = prev ? words(prev).map(singular) : [];
		const object = rest.length > 0 ? rest : owner;
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
	if (verb === 'List') return `List ${phrase(noun)}${suffix}`;
	const one = [...noun.slice(0, -1), singular(noun[noun.length - 1] ?? '')];
	return `${verb} ${article(one)} ${phrase(one)}${suffix}`;
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
