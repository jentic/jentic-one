/**
 * Actor directory — repository tier for the unified actor lookup endpoints.
 *
 * Executions, audit entries, and the events feed carry an
 * opaque `actor_id` (a KSUID like `agnt_6a3d3c62…`). These wrappers map those
 * ids to friendly names instead of rendering the raw token, through one of two
 * endpoints:
 *
 *   - `GET /actors` (`users:read`): the whole directory, paged through once via
 *     `next_cursor` and cached aggressively by the query layer.
 *   - `GET /actors/lookup?id=…` (any signed-in caller): only the ids asked for,
 *     at most {@link ACTOR_LOOKUP_MAX_IDS} per call. {@link loadActor} batches
 *     the ids requested in the same tick into as few calls as possible.
 *
 * Scope is the directory's own actor types — `user` / `agent`. A legacy
 * `tk_…` or `sva_…` id can still appear as the `actor_id` of a historical
 * execution or audit row; rendering that case is `<ActorLabel>`'s job. Other non-actor ids (`cred_`, `exec_`, `areq_`, `job_`)
 * are resolved separately and are out of scope here.
 */
import {
	ActorsService,
	type ActorLookupEntryResponse,
	type ActorSummaryResponse,
} from '@/shared/api';

/** Max page size the backend accepts (`limit` 1..5000). */
const PAGE_LIMIT = 5000;

/** Max ids one `GET /actors/lookup` call accepts. */
export const ACTOR_LOOKUP_MAX_IDS = 100;

/** The fields both endpoints return for an actor. */
export type ActorDirectoryEntry = ActorLookupEntryResponse;

/**
 * Fetch every actor by following `next_cursor` until the backend reports no
 * more pages. Returns the flat list of actor summaries; the query hook turns
 * this into a lookup map.
 *
 * The loop can never spin forever, even against a misbehaving backend: a
 * `has_more: true` with a null cursor ends it, and a cursor we've already
 * followed (a backend stuck returning the same `next_cursor`) ends it too.
 */
export async function fetchActorDirectory(): Promise<ActorSummaryResponse[]> {
	const actors: ActorSummaryResponse[] = [];
	const seenCursors = new Set<string>();
	let cursor: string | null = null;

	do {
		const page = await ActorsService.listActors({ cursor, limit: PAGE_LIMIT });
		actors.push(...page.data);
		const next = page.has_more ? (page.next_cursor ?? null) : null;
		// Stop if the backend hands back a cursor we've already followed —
		// otherwise a stuck cursor would loop (and silently re-dedup) forever.
		cursor = next !== null && seenCursors.has(next) ? null : next;
		if (cursor !== null) seenCursors.add(cursor);
	} while (cursor !== null);

	return actors;
}

/**
 * Resolve the given ids through `GET /actors/lookup`, splitting them into
 * calls of at most {@link ACTOR_LOOKUP_MAX_IDS}. Ids that match no actor are
 * absent from the result.
 */
export async function lookupActors(ids: readonly string[]): Promise<ActorDirectoryEntry[]> {
	const unique = [...new Set(ids)];
	const chunks: string[][] = [];
	for (let i = 0; i < unique.length; i += ACTOR_LOOKUP_MAX_IDS) {
		chunks.push(unique.slice(i, i + ACTOR_LOOKUP_MAX_IDS));
	}
	const pages = await Promise.all(chunks.map((id) => ActorsService.lookupActors({ id })));
	return pages.flatMap((page) => page.data);
}

interface Waiter {
	resolve: (actor: ActorDirectoryEntry | null) => void;
	reject: (error: unknown) => void;
}

let pending = new Map<string, Waiter[]>();
let flushScheduled = false;

async function flushPending(): Promise<void> {
	const batch = pending;
	pending = new Map();
	flushScheduled = false;
	try {
		const found = new Map((await lookupActors([...batch.keys()])).map((a) => [a.id, a]));
		for (const [id, waiters] of batch) {
			for (const waiter of waiters) waiter.resolve(found.get(id) ?? null);
		}
	} catch (error) {
		for (const waiters of batch.values()) {
			for (const waiter of waiters) waiter.reject(error);
		}
	}
}

/**
 * Resolve one actor id, coalescing every id requested in the same tick into
 * one batched {@link lookupActors} call. Resolves to `null` for an id that
 * matches no actor, so callers can cache the miss.
 */
export function loadActor(id: string): Promise<ActorDirectoryEntry | null> {
	return new Promise((resolve, reject) => {
		const waiters = pending.get(id);
		if (waiters) waiters.push({ resolve, reject });
		else pending.set(id, [{ resolve, reject }]);
		if (!flushScheduled) {
			flushScheduled = true;
			setTimeout(() => void flushPending(), 0);
		}
	});
}
