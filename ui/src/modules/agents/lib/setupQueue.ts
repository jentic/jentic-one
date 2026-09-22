/**
 * Setup-queue state — the pure layer behind the Add-APIs queue, which walks the
 * tray's preflighted batch one API at a time. There is no `Skip for now`, so
 * `reuse` items are bound first (they must not queue behind a form being typed)
 * and an unsettled item is handed back on re-entry rather than lost.
 */
import type { Credential, SelectedApi } from '@/shared/credentials/api';
import type { PreflightItem, PreflightOutcome } from '@/modules/agents/lib/apiPreflight';

/** Where one API is in the queue: `active` = the pane is on it, `working` = a
 * request is in flight, `added`/`dropped` are terminal, `failed` is retryable but
 * not walked past. */
export type QueueStatus = 'waiting' | 'active' | 'working' | 'added' | 'dropped' | 'failed';

export interface QueueEntry {
	/** `apiRefKey` — same identity the tray and the preflight use. */
	key: string;
	api: SelectedApi;
	outcome: PreflightOutcome;
	/** Org credentials that cover this API, from the preflight. */
	candidates: Credential[];
	/** Accepting this item imports the API into the workspace. */
	importsApi: boolean;
	status: QueueStatus;
	/** The credential this item ended up bound through. */
	credentialId?: string;
	/** That credential's label, shown on the finished row. A `reuse` item binds
	 * without a pane, so without it the operator is never told which was chosen. */
	credentialName?: string;
	/** Why it failed, shown on the row next to `Try again`. */
	error?: string;
	/** An honest qualifier on a terminal row — e.g. an OAuth sign-in that was
	 *  never finished, which leaves the API attached but unable to serve. */
	note?: string;
}

/** Statuses the queue no longer ADVANCES past — a failure must not stall the
 * items behind it. Not the same question as {@link isResolved}. */
export function isTerminal(status: QueueStatus): boolean {
	return status === 'added' || status === 'dropped' || status === 'failed';
}

/** Statuses that SETTLE what happens to the API: attached (`added`), or the
 * operator was told it would not be (`dropped`). `failed` is absent — nobody
 * chose it, so the item is still outstanding work. */
export function isResolved(status: QueueStatus): boolean {
	return status === 'added' || status === 'dropped';
}

/** Does this item need the operator in a pane, or can it be bound outright? */
export function needsPane(outcome: PreflightOutcome): boolean {
	return outcome !== 'reuse';
}

/** Build the queue from the tray's batch. `reuse` items come first, so a
 * mostly-reuse batch feels like one click. */
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

/** Put the active item into `active` so the pane and the progress list agree.
 * Pure and idempotent, so it is safe to run on every render. */
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
	/** Items not yet walked — still `waiting`, `active` or `working`. */
	unfinished: number;
	total: number;
	/** Items the host hands back on close: the unwalked ones plus the failures — the
	 * figure the progress bar and the "N wait here for next time" note read. */
	remaining: number;
	/** Nothing is left to hand back — every API is attached or declined. */
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
	const remaining = unfinished + failed;
	return {
		added,
		dropped,
		failed,
		unfinished,
		total: entries.length,
		remaining,
		done: remaining === 0,
	};
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

/** What dropping an item means, in the operator's words. Without a skip path a
 * drop is not "later": the API is simply not attached. */
export function dropWarning(label: string): string {
	return `${label} won't be added.`;
}

/** The items still owed to the operator, back in `PreflightItem` shape so the
 * host can reopen the queue on the remainder. Filtered on {@link isResolved},
 * not {@link isTerminal}: a failed item dropped here is lost silently. */
export function unfinishedItems(entries: QueueEntry[]): PreflightItem[] {
	return entries
		.filter((e) => !isResolved(e.status))
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

/** What a freshly added API can do — a binding is created with no rules, the
 * broker's default-deny state, so the agent reaches it only once rules exist. */
export const QUEUE_RULES_NOTICE =
	'Added APIs start with no access rules, so calls are blocked until you add rules on the API.';
