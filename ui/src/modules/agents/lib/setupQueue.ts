/**
 * Setup-queue state — the pure layer behind the Add-APIs queue, which walks the
 * tray's preflighted batch one API at a time, in pick order. Every item stops in
 * a pane — nothing is bound without the operator confirming it — and, as there
 * is no `Skip for now`, an unsettled item is handed back on re-entry rather than
 * lost.
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
	/** Every org credential that covers this API, from the preflight — the
	 * existing options the pane offers alongside a new credential. */
	covering: Credential[];
	/** Accepting this item imports the API into the workspace. */
	importsApi: boolean;
	status: QueueStatus;
	/** The credential this item ended up bound through. */
	credentialId?: string;
	/** That credential's label, shown on the finished row so the record says
	 * which credential each API went through. */
	credentialName?: string;
	/** A credential this queue created for the item. Kept so `Try again` after a
	 * failed bind binds it, instead of opening the wizard to create another. */
	created?: { credential_id: string; name: string; needsConnect: boolean };
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

/** Build the queue from the tray's batch, in pick order. */
export function buildQueue(items: PreflightItem[]): QueueEntry[] {
	return items.map(entryFor);
}

/** A fresh `waiting` entry for one preflighted pick. */
function entryFor(item: PreflightItem): QueueEntry {
	return {
		key: item.key,
		api: item.api,
		outcome: item.outcome,
		covering: item.covering,
		importsApi: item.importsApi,
		status: 'waiting',
	};
}

/**
 * Fold a re-edited batch — the operator went Back to the tray and returned — into
 * the queue without losing progress. A settled entry is never lost: `added` always
 * stays (going back never undoes a saved binding, and the tray locks those rows),
 * and a declined one stays declined unless it was re-ticked, which puts it back in
 * line. An unsettled entry still in the batch keeps its place and status, with its
 * preflight facts refreshed (a credential may have been created meanwhile); one
 * that was unticked is removed. New picks join at the end.
 */
export function reconcileQueue(entries: QueueEntry[], items: PreflightItem[]): QueueEntry[] {
	const byKey = new Map(items.map((item) => [item.key, item]));
	const kept: QueueEntry[] = [];
	for (const entry of entries) {
		const item = byKey.get(entry.key);
		if (entry.status === 'added' || (entry.status === 'dropped' && !item)) {
			kept.push(entry);
			continue;
		}
		if (!item) continue;
		kept.push(
			entry.status === 'dropped'
				? { ...entryFor(item), created: entry.created }
				: {
						...entry,
						api: item.api,
						outcome: item.outcome,
						covering: item.covering,
						importsApi: item.importsApi,
					},
		);
	}
	const known = new Set(kept.map((e) => e.key));
	return [...kept, ...items.filter((item) => !known.has(item.key)).map(entryFor)];
}

/** What the tray is seeded with when the operator goes Back: the APIs still owed
 * (ticked, editable) and the ones this batch already added (ticked, locked). A
 * declined API is left unticked — "Not added" was already the answer. */
export interface QueueBackSeed {
	picks: SelectedApi[];
	added: SelectedApi[];
}

export function queueBackSeed(entries: QueueEntry[]): QueueBackSeed {
	return {
		picks: entries.filter((e) => !isResolved(e.status)).map((e) => e.api),
		added: entries.filter((e) => e.status === 'added').map((e) => e.api),
	};
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
			covering: e.covering,
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
