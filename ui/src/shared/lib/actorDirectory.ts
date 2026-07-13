/**
 * Actor directory — repository tier for the unified actor lookup endpoint
 * (`GET /actors`, PR #483, closes #478).
 *
 * Executions, audit entries, the events feed, and access requests carry an
 * opaque `actor_id` (a KSUID like `agnt_6a3d3c62…`). This wrapper hydrates the
 * full actor directory so any surface can map those ids to friendly names
 * instead of rendering the raw token.
 *
 * The directory is small relative to executions and is designed for bulk cache
 * hydration (large default page size), so we page through ALL pages via
 * `next_cursor` once and let the query layer cache the result aggressively.
 *
 * Scope is the directory's own actor types — `user` / `agent` /
 * `service_account`. The backend `GET /actors` UNION deliberately excludes
 * toolkits ("Toolkits are not platform actors"), even though a `tk_…` id can
 * appear as the `actor_id` of a broker-path execution; rendering that case is
 * `<ActorLabel>`'s job. Other non-actor ids (`cred_`, `exec_`, `areq_`, `job_`)
 * are resolved separately and are out of scope here.
 */
import { ActorsService, type ActorSummaryResponse } from '@/shared/api';

/** Max page size the backend accepts (`limit` 1..5000). */
const PAGE_LIMIT = 5000;

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
