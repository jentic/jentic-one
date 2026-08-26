/**
 * Delivery-health derivations for the webhooks LIST surface.
 *
 * Health is not served as an aggregate — the only source is the per-endpoint
 * `/stats` endpoint (the same one the detail Overview reads). These pure helpers
 * turn one endpoint's {@link WebhookEndpointStats} into the scannable signals a
 * list row and the overview strip both show, and roll several endpoints' stats
 * into workspace-wide totals for the strip.
 *
 * Everything degrades on missing data rather than inventing it: an endpoint
 * whose stats have not loaded (or failed) contributes nothing to the totals and
 * renders as an "unknown" pill, never a fake 100%.
 */
import type { WebhookEndpointStats } from '@/modules/webhooks/api';

/** A row/tile health verdict — text + tone, never colour alone. */
export type HealthTone = 'healthy' | 'degraded' | 'failing' | 'idle' | 'unknown';

export interface EndpointHealth {
	tone: HealthTone;
	/** Short human label ("100% healthy", "2 failing", "No deliveries"). */
	label: string;
	/** All-time success rate as a 0–100 integer, or null when nothing sent. */
	successRate: number | null;
	/** Deliveries in the last 24h. */
	recentTotal: number;
	/** Failures (failed + dead) in the last 24h. */
	recentFailed: number;
	/** Dead-lettered deliveries (all-time) — the "needs attention" count. */
	deadLettered: number;
	/** Currently-retrying deliveries (all-time). */
	retrying: number;
	/** Most recent attempt time, or null. */
	lastAttemptAt: string | null;
}

function successRate(stats: WebhookEndpointStats): number | null {
	if (stats.total <= 0) return null;
	const bad = (stats.countsByStatus.dead ?? 0) + (stats.countsByStatus.failed ?? 0);
	return Math.round(((stats.total - bad) / stats.total) * 100);
}

/**
 * Reduce one endpoint's stats to a row-level health verdict. `stats` is
 * `undefined` while its query is in flight or after it failed — that case maps
 * to `unknown` so the row shows a neutral pill instead of a fabricated number.
 */
export function endpointHealth(stats: WebhookEndpointStats | undefined): EndpointHealth {
	if (!stats) {
		return {
			tone: 'unknown',
			label: 'Health unknown',
			successRate: null,
			recentTotal: 0,
			recentFailed: 0,
			deadLettered: 0,
			retrying: 0,
			lastAttemptAt: null,
		};
	}

	const dead = stats.countsByStatus.dead ?? 0;
	const failed = stats.countsByStatus.failed ?? 0;
	const rate = successRate(stats);

	let tone: HealthTone;
	let label: string;
	if (stats.total === 0) {
		tone = 'idle';
		label = 'No deliveries yet';
	} else if (dead > 0) {
		tone = 'failing';
		label = `${dead} dead-lettered`;
	} else if (failed > 0 || (rate != null && rate < 100)) {
		tone = 'degraded';
		label = rate != null ? `${rate}% healthy` : 'Retrying';
	} else {
		tone = 'healthy';
		label = '100% healthy';
	}

	return {
		tone,
		label,
		successRate: rate,
		recentTotal: stats.recentTotal,
		recentFailed: stats.recentFailed,
		deadLettered: dead,
		retrying: failed,
		lastAttemptAt: stats.lastAttemptAt,
	};
}

/** Workspace-wide totals for the overview strip, aggregated across endpoints. */
export interface WebhooksSummary {
	totalEndpoints: number;
	activeEndpoints: number;
	pausedEndpoints: number;
	/** Deliveries across all endpoints in the last 24h. */
	recentTotal: number;
	recentFailed: number;
	/** Last-24h success rate as a 0–100 integer, or null when nothing sent. */
	recentSuccessRate: number | null;
	/** Dead-lettered deliveries needing attention, summed across endpoints. */
	deadLettered: number;
	/** Currently-retrying deliveries, summed across endpoints. */
	retrying: number;
	/** How many endpoints' stats have actually loaded (for degraded captions). */
	statsLoaded: number;
	/** True when at least one endpoint's stats are still missing. */
	partial: boolean;
}

/**
 * Roll several endpoints' stats into workspace totals. `statsById` may be sparse
 * — endpoints whose stats have not loaded are simply skipped for the
 * delivery-derived tiles (and counted in `partial`), so the strip shows real
 * partial totals rather than waiting on the slowest query or faking a full one.
 */
export function summariseEndpoints(
	endpoints: { id: string; active: boolean }[],
	statsById: Map<string, WebhookEndpointStats | undefined>,
): WebhooksSummary {
	let recentTotal = 0;
	let recentFailed = 0;
	let deadLettered = 0;
	let retrying = 0;
	let statsLoaded = 0;

	for (const endpoint of endpoints) {
		const stats = statsById.get(endpoint.id);
		if (!stats) continue;
		statsLoaded += 1;
		recentTotal += stats.recentTotal;
		recentFailed += stats.recentFailed;
		deadLettered += stats.countsByStatus.dead ?? 0;
		retrying += stats.countsByStatus.failed ?? 0;
	}

	const activeEndpoints = endpoints.filter((e) => e.active).length;

	return {
		totalEndpoints: endpoints.length,
		activeEndpoints,
		pausedEndpoints: endpoints.length - activeEndpoints,
		recentTotal,
		recentFailed,
		recentSuccessRate:
			recentTotal > 0 ? Math.round(((recentTotal - recentFailed) / recentTotal) * 100) : null,
		deadLettered,
		retrying,
		statsLoaded,
		partial: statsLoaded < endpoints.length,
	};
}
