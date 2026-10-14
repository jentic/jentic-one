/**
 * Usage-aggregation transformers — map `GET /monitoring/usage` responses
 * (`UsageResponse`) into the UI-shaped rows the Usage charts render.
 * A single entity-row shape covers every grouping dimension, since the
 * endpoint returns the same `{key,label,total,success,
 * failed,avg_ms,trend}` rows for each.
 */
import type { UsageResponse } from '@/modules/monitor/api';
import {
	RETIRED_SERVICE_ACCOUNT_ACTOR_TYPE,
	retiredServiceAccountLabel,
	shortActorId,
} from '@/shared/lib';

/** Row id / label of the bucket for rows with no attributable key. */
export const UNATTRIBUTED_ID = '__unattributed__';
export const UNATTRIBUTED_LABEL = 'Unattributed';

/** Overall window stats, UI vocabulary (rates in 0–100 percent). */
export interface UsageOverview {
	totalExecutions: number;
	/**
	 * Executions with status COMPLETED. Not derivable as `total - failures`:
	 * backend `total` is count(*), so in-flight (pending/running) executions
	 * are in `total` but neither success nor failed.
	 */
	successCount: number;
	successRate: number;
	failureCount: number;
	avgLatencyMs: number;
	p50Ms: number | null;
	p95Ms: number | null;
}

/** One api / credential / agent row for the bubble chart + breakdown table. */
export interface EntityUsageRow {
	id: string;
	label: string;
	totalExecutions: number;
	successRate: number;
	avgLatencyMs: number;
	trend: number[];
	/**
	 * Agents lens only, for a row with a live actor behind it: that actor's id
	 * and how far its name resolved (see {@link withActorNames}).
	 */
	actor?: { id: string; type: string; resolution: ActorNameResolution };
	/** The avatar letters, when they come from the fleet rather than the label. */
	initials?: string;
}

/**
 * How an agents-lens row's name resolved: `resolved` (the directory named it),
 * `loading` (the directory is still being read), `unknown` (no actor by that
 * id — deleted, or never listed), `hidden` (an agent the caller may not see).
 */
export type ActorNameResolution = 'resolved' | 'loading' | 'unknown' | 'hidden';

/**
 * A row whose actor has no name to show yet or at all (`loading`, `unknown`):
 * its label is the actor's id, set in mono like every other unresolved id. A
 * `hidden` row reads as a sentence, so it keeps the text face.
 */
export function isUnnamedActor(row: Pick<EntityUsageRow, 'actor'>): boolean {
	return row.actor?.resolution === 'loading' || row.actor?.resolution === 'unknown';
}

/** The label of an agent the caller may not see. */
export const HIDDEN_AGENT_LABEL = 'Agent (not visible to you)';

/** What {@link withActorNames} needs from the actor directory. */
export interface ActorNameSource {
	resolve: (id: string) => string | undefined;
	isHidden: (id: string) => boolean;
	isLoading: boolean;
	/** Fleet-aware agent initials by actor id (`smartInitials`). */
	initials: ReadonlyMap<string, string>;
}

/** Shown on an avatar whose name is not known, rather than letters of an id. */
const UNKNOWN_INITIALS = '?';

/** The usage key's `actor_type` for an agent. */
const AGENT_ACTOR_TYPE = 'agent';

export function usageToOverview(usage: UsageResponse): UsageOverview {
	const total = usage.stats.total ?? 0;
	const success = usage.stats.success ?? 0;
	const failed = usage.stats.failed ?? 0;
	return {
		totalExecutions: total,
		successCount: success,
		successRate: total > 0 ? (success / total) * 100 : 100,
		failureCount: failed,
		avgLatencyMs: Math.round(usage.stats.avg_ms ?? 0),
		p50Ms: usage.stats.p50_ms != null ? Math.round(usage.stats.p50_ms) : null,
		p95Ms: usage.stats.p95_ms != null ? Math.round(usage.stats.p95_ms) : null,
	};
}

/**
 * Format a top-row key into a display label, per grouping dimension. The
 * backend composes keys/labels mechanically (see monitoring_repo.grouped_top):
 *   api        → "vendor/name" with NULL columns coalesced to "unknown"
 *   credential → the raw credential_id (NULL coalesced to "unknown")
 *   agent      → "actor_type/actor_id" (both NOT NULL columns). Historical
 *                `service_account/sva_…` keys (executions recorded before the
 *                service-account migration) are labelled
 *                "sva_… (retired service account)".
 * Null/unknown keys are surfaced as an explicit "Unattributed" bucket.
 */
function formatEntityLabel(groupBy: string, key: string | null | undefined): string {
	if (!key) return UNATTRIBUTED_LABEL;
	if (groupBy === 'api') {
		const [vendor, ...rest] = key.split('/');
		const name = rest.join('/');
		if (vendor === 'unknown' && (name === 'unknown' || name === '')) return UNATTRIBUTED_LABEL;
		return name && name !== 'unknown' ? name : key;
	}
	if (groupBy === 'agent') {
		const slash = key.indexOf('/');
		if (slash < 0) return key;
		const actorId = key.slice(slash + 1);
		if (!actorId) return UNATTRIBUTED_LABEL;
		return key.slice(0, slash) === RETIRED_SERVICE_ACCOUNT_ACTOR_TYPE
			? retiredServiceAccountLabel(actorId)
			: actorId;
	}
	if (groupBy === 'credential') {
		return key === 'unknown' ? UNATTRIBUTED_LABEL : key;
	}
	return key;
}

/**
 * Map the response's `top` rows into entity rows, sorted busiest-first.
 * The attribution columns are NOT NULL so keys are always present today;
 * empty/missing keys are still mapped to an explicit "Unattributed" bucket
 * as a display fallback rather than silently dropped.
 */
export function usageToEntityRows(usage: UsageResponse | undefined): EntityUsageRow[] {
	if (!usage) return [];
	return usage.top
		.map((row) => {
			const total = row.total ?? 0;
			const success = row.success ?? 0;
			const key = (row.key ?? null) as string | null;
			return {
				id: key || UNATTRIBUTED_ID,
				label: formatEntityLabel(usage.group_by, key),
				totalExecutions: total,
				successRate: total > 0 ? (success / total) * 100 : 100,
				avgLatencyMs: Math.round(row.avg_ms ?? 0),
				trend: row.trend ?? [],
			};
		})
		.sort((a, b) => b.totalExecutions - a.totalExecutions);
}

/**
 * Name the agents-lens rows: the usage rollup keys a row by
 * `actor_type/actor_id` and carries no name, so each live actor is looked up in
 * the actor directory the rest of the app labels actors from. A named row reads
 * its name, with the fleet's initials for an agent. While the directory loads,
 * or for an id it does not hold, the row reads the short id (`agnt_…e4d9`) —
 * never the whole id as if it were a name — with a `?` avatar. Retired
 * service-account and Unattributed rows have no live actor and stay as they are.
 */
export function withActorNames(
	rows: readonly EntityUsageRow[],
	source: ActorNameSource,
): EntityUsageRow[] {
	return rows.map((row) => {
		const slash = row.id.indexOf('/');
		if (row.id === UNATTRIBUTED_ID || slash < 0) return row;
		const type = row.id.slice(0, slash);
		const id = row.id.slice(slash + 1);
		if (!id || type === RETIRED_SERVICE_ACCOUNT_ACTOR_TYPE) return row;
		const name = source.resolve(id);
		if (name) {
			return {
				...row,
				label: name,
				actor: { id, type, resolution: 'resolved' },
				initials: type === AGENT_ACTOR_TYPE ? source.initials.get(id) : undefined,
			};
		}
		if (type === AGENT_ACTOR_TYPE && source.isHidden(id)) {
			return {
				...row,
				label: HIDDEN_AGENT_LABEL,
				actor: { id, type, resolution: 'hidden' },
				initials: UNKNOWN_INITIALS,
			};
		}
		return {
			...row,
			label: shortActorId(id),
			actor: { id, type, resolution: source.isLoading ? 'loading' : 'unknown' },
			initials: UNKNOWN_INITIALS,
		};
	});
}
