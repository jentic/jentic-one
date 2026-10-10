/**
 * What a "Can call" row says about its traffic, derived from reads the page
 * already makes: the actor's 7-day usage of each API grouped by credential
 * (`/monitoring/usage`) and its newest executions (`/executions`). No figure
 * here is invented — a missing read leaves its figure out.
 */
import type { ActorApiUsage, ActorExecutionEntity, ApiCredentialUsage } from '@/modules/agents/api';
import type { ApiTileModel } from '@/modules/agents/lib/apiTiles';

/** The `api_id` `/monitoring/usage` filters by (`vendor/name`); null for a
 * vendor-wide binding, which names no single API. */
export function tileUsageApiId(tile: Pick<ApiTileModel, 'vendor' | 'apiName'>): string | null {
	return tile.apiName ? `${tile.vendor}/${tile.apiName}` : null;
}

/** Case-insensitive identity, as the binding scope matcher compares it. */
function same(a: string | null | undefined, b: string | null | undefined): boolean {
	return (a ?? '').toLowerCase() === (b ?? '').toLowerCase();
}

/** The calls a row stands for: through its credential, to its API (any API
 * of the vendor for a vendor-wide row). Newest first, as the feed returns them. */
function rowCalls(
	tile: Pick<ApiTileModel, 'credentialId' | 'vendor' | 'apiName'>,
	items: readonly ActorExecutionEntity[],
): ActorExecutionEntity[] {
	return items.filter(
		(call) =>
			call.credentialId === tile.credentialId &&
			call.api != null &&
			same(call.api.vendor, tile.vendor) &&
			(tile.apiName == null || same(call.api.name, tile.apiName)),
	);
}

/** "412 ms" / "1.2 s"; null when the call recorded no duration. */
export function formatDurationMs(ms: number | null | undefined): string | null {
	if (ms == null || !Number.isFinite(ms)) return null;
	if (ms >= 1000) return `${(ms / 1000).toFixed(1).replace(/\.0$/, '')} s`;
	return `${Math.round(ms)} ms`;
}

export interface ApiRowActivity {
	/** This row's credential's 7-day calls on the API: `undefined` while loading,
	 * `null` when not visible to this viewer (gated or failed). A credential
	 * absent from a loaded rollup made no calls — a zero, not a gap. */
	usage: ApiCredentialUsage | null | undefined;
	/** The API-wide latency percentiles (every credential), when read. */
	percentiles: Pick<ActorApiUsage, 'p50Ms' | 'p95Ms'> | null;
	/** The row's calls from the agent's newest executions, newest first;
	 * `undefined` while loading, `null` when not visible. */
	calls: ActorExecutionEntity[] | null | undefined;
	/** How many of the agent's newest calls (every API) `calls` was picked
	 * from, and whether older ones exist past them; null until read. An empty
	 * `calls` only means "no calls" when nothing lies past the scan. */
	scanned: { count: number; hasMore: boolean } | null;
}

/**
 * Whether an empty `calls` is the whole story. The feed is the agent's newest
 * calls across every API, so a row whose calls are older than that window (or
 * that the 7-day rollup counts) has calls the scan never reached.
 */
export function callsBeyondScan(activity: ApiRowActivity): boolean {
	if (!activity.calls || activity.calls.length > 0) return false;
	return Boolean(activity.scanned?.hasMore) || (activity.usage?.total ?? 0) > 0;
}

/** A credential absent from a loaded per-credential rollup made no calls. */
const NO_CALLS: ApiCredentialUsage = { total: 0, success: 0, failed: 0, avgMs: 0, trend: [] };

/**
 * One row's traffic from the page's two reads: the per-API usage rollups
 * (`useActorApiUsage`, keyed by `tileUsageApiId`) and the agent's newest calls
 * (`useActorRecentCalls`: `undefined` loading, `null` gated or failed).
 */
export function rowActivity(
	tile: Pick<ApiTileModel, 'credentialId' | 'vendor' | 'apiName'>,
	usageByApi: ReadonlyMap<string, ActorApiUsage | null>,
	recent: { items: ActorExecutionEntity[]; hasMore?: boolean } | null | undefined,
): ApiRowActivity {
	const apiId = tileUsageApiId(tile);
	// A vendor-wide row names no single API, so no rollup answers for it.
	const rollup = apiId == null ? null : usageByApi.get(apiId);
	return {
		usage:
			rollup === undefined
				? undefined
				: rollup === null
					? null
					: (rollup.byCredential.get(tile.credentialId) ?? NO_CALLS),
		percentiles: rollup ? { p50Ms: rollup.p50Ms, p95Ms: rollup.p95Ms } : null,
		calls:
			recent === undefined
				? undefined
				: recent === null
					? null
					: rowCalls(tile, recent.items),
		scanned: recent ? { count: recent.items.length, hasMore: Boolean(recent.hasMore) } : null,
	};
}
