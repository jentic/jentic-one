/**
 * Setup-queue state — the pure layer behind the Add-APIs queue (plan §4.4).
 *
 * The queue takes the tray's preflighted batch and walks it one API at a time
 * until every item is finished or dropped. There is no `Skip for now` (D13), so
 * the two things this layer has to get exactly right are ORDER and TERMINALITY:
 *
 *  - Order: `reuse` items bypass the pane entirely and are bound first. That is
 *    the mechanic that collapses most of the work, so they must not queue up
 *    behind a form the operator is still typing.
 *  - Terminality: an item is `added` or `dropped`, or it failed and can be
 *    retried. Anything that never reached one of those is UNFINISHED, and the
 *    host has to hand it back on re-entry — the queue is the only way an API
 *    arrives, so a dismissal cannot quietly lose it.
 *
 * Framework-free, so both rules are unit-testable without a DOM.
 */
import type { Credential, SelectedApi } from '@/shared/credentials/api';
import type { PreflightItem, PreflightOutcome } from '@/modules/agents/lib/apiPreflight';

/**
 * Where one API is in the queue.
 *
 * - `waiting` — not reached yet.
 * - `active` — the pane is on this item (or it is next to be auto-bound).
 * - `working` — a request is in flight for it.
 * - `added` — bound to the agent. Terminal.
 * - `dropped` — the operator declined it, so the API is NOT attached. Terminal.
 * - `failed` — a request failed. Terminal for advancement, but retryable: a
 *   partial failure must not stall the items behind it.
 */
export type QueueStatus = 'waiting' | 'active' | 'working' | 'added' | 'dropped' | 'failed';

export interface QueueEntry {
	/** `apiRefKey` — same identity the tray and the preflight use. */
	key: string;
	api: SelectedApi;
	outcome: PreflightOutcome;
	/** Org credentials that cover this API, from the preflight. */
	candidates: Credential[];
	/** Accepting this item imports the API into the workspace (D5). */
	importsApi: boolean;
	status: QueueStatus;
	/** The credential this item ended up bound through. */
	credentialId?: string;
	/**
	 * That credential's label, shown on the finished row.
	 *
	 * A `reuse` item is bound without ever showing a pane, so without the name
	 * the operator is told an API was added and never which of their credentials
	 * the flow chose for it. Naming it is what keeps the silent path honest.
	 */
	credentialName?: string;
	/** Why it failed, shown on the row next to `Try again`. */
	error?: string;
	/** An honest qualifier on a terminal row — e.g. an OAuth sign-in that was
	 *  never finished, which leaves the API attached but unable to serve. */
	note?: string;
}

/** Statuses the queue no longer advances past. */
export function isTerminal(status: QueueStatus): boolean {
	return status === 'added' || status === 'dropped' || status === 'failed';
}

/** Does this item need the operator in a pane, or can it be bound outright? */
export function needsPane(outcome: PreflightOutcome): boolean {
	return outcome !== 'reuse';
}

/**
 * Build the queue from the tray's batch.
 *
 * `reuse` items come first — they need no attention, so binding them up front
 * is what makes a mostly-reuse batch feel like one click. Everything else keeps
 * the operator's pick order.
 */
export function buildQueue(items: PreflightItem[]): QueueEntry[] {
	const entry = (item: PreflightItem): QueueEntry => ({
		key: item.key,
		api: item.api,
		outcome: item.outcome,
		candidates: item.candidates,
		importsApi: item.importsApi,
		status: 'waiting',
	});
	return [
		...items.filter((i) => !needsPane(i.outcome)).map(entry),
		...items.filter((i) => needsPane(i.outcome)).map(entry),
	];
}

/** The item the queue is on: the first that has not reached a terminal state. */
export function activeEntry(entries: QueueEntry[]): QueueEntry | null {
	return entries.find((e) => !isTerminal(e.status)) ?? null;
}

/** Replace one entry, leaving the rest untouched. */
export function patchEntry(
	entries: QueueEntry[],
	key: string,
	patch: Partial<QueueEntry>,
): QueueEntry[] {
	return entries.map((e) => (e.key === key ? { ...e, ...patch } : e));
}

/**
 * Put the active item into `active` so the pane and the progress list agree on
 * which row is live. Pure and idempotent: it returns the same array when
 * nothing needs moving, so it is safe to run on every render.
 */
export function markActive(entries: QueueEntry[]): QueueEntry[] {
	const active = activeEntry(entries);
	if (!active || active.status !== 'waiting') return entries;
	return patchEntry(entries, active.key, { status: 'active' });
}

/** Retry a failed item by sending it back to the front of the unfinished work. */
export function retryEntry(entries: QueueEntry[], key: string): QueueEntry[] {
	return patchEntry(entries, key, { status: 'waiting', error: undefined });
}

export interface QueueSummary {
	added: number;
	dropped: number;
	failed: number;
	/** Items with no terminal state yet — what re-entry has to resume. */
	unfinished: number;
	total: number;
	/** Every item reached a terminal state. */
	done: boolean;
}

export function queueSummary(entries: QueueEntry[]): QueueSummary {
	let added = 0;
	let dropped = 0;
	let failed = 0;
	let unfinished = 0;
	for (const e of entries) {
		if (e.status === 'added') added += 1;
		else if (e.status === 'dropped') dropped += 1;
		else if (e.status === 'failed') failed += 1;
		else unfinished += 1;
	}
	return { added, dropped, failed, unfinished, total: entries.length, done: unfinished === 0 };
}

/** The one-line outcome, e.g. `2 APIs added · 1 dropped`. Empty when nothing happened. */
export function queueSummaryLine(summary: QueueSummary): string {
	const parts: string[] = [];
	if (summary.added > 0) {
		parts.push(`${summary.added} ${summary.added === 1 ? 'API' : 'APIs'} added`);
	}
	if (summary.dropped > 0) parts.push(`${summary.dropped} dropped`);
	if (summary.failed > 0) parts.push(`${summary.failed} failed`);
	return parts.join(' · ');
}

/**
 * What dropping an item means, in the operator's words.
 *
 * Without a skip path (D13) a drop is not "later" — the API is simply not
 * attached, and the copy has to say so before anyone waits for it to come back.
 */
export function dropWarning(label: string): string {
	return `${label} won't be added.`;
}

/**
 * The unfinished items, back in `PreflightItem` shape so the host can stash
 * them and reopen the queue on the remainder.
 *
 * The preflight facts (candidates, import flag) are the ones the tray computed
 * for this batch; they are re-derived by the tray on a fresh pass, so resuming
 * from them is correct for as long as the batch is in flight.
 */
export function unfinishedItems(entries: QueueEntry[]): PreflightItem[] {
	return entries
		.filter((e) => !isTerminal(e.status))
		.map((e) => ({
			key: e.key,
			api: e.api,
			outcome: e.outcome,
			candidates: e.candidates,
			importsApi: e.importsApi,
		}));
}

/** Row copy for a terminal or in-flight status. */
export const QUEUE_STATUS_LABELS: Record<QueueStatus, string> = {
	waiting: 'Waiting',
	active: 'Now',
	working: 'Adding…',
	added: 'Added',
	dropped: 'Not added',
	failed: 'Failed',
};

/**
 * What a freshly added API can actually do — stated because it is not what an
 * operator assumes. A binding is created with no rules (least privilege, C1),
 * which is the broker's default-deny state, so the agent reaches the API only
 * once rules exist. The tile says the same thing ("No rules — all calls
 * blocked"); the queue says it at the moment it becomes true.
 */
export const QUEUE_RULES_NOTICE =
	'Added APIs start with no access rules, so calls are blocked until you add rules on the API.';
